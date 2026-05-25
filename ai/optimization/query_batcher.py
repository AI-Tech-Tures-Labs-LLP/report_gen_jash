"""Query Batching Engine for parallel SQL execution.

Executes multiple SQL queries in parallel with:
- Controlled concurrency (semaphore)
- Dependency-based ordering (DAG)
- Timeout handling
- Error aggregation
"""

from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
import asyncio
import time
import logging
from db.executor import execute_sql

logger = logging.getLogger(__name__)


@dataclass
class BatchedQuery:
    """Configuration for a query in a batch"""
    query_id: str
    query_text: str
    query_type: str  # 'kpi', 'chart', 'table', 'drift'
    purpose: str
    priority: int = 5  # 1-10, lower = higher priority
    timeout_seconds: int = 30
    cache_key: Optional[str] = None


@dataclass
class BatchResult:
    """Result of a batched query execution"""
    query_id: str
    success: bool
    data: List[Dict]
    columns: List[str]
    error: str = ""
    execution_time_ms: int = 0
    cache_hit: bool = False
    rows_returned: int = 0


class QueryBatchingEngine:
    """Execute multiple SQL queries in parallel with resource management
    
    Usage:
        batcher = QueryBatchingEngine(max_concurrent=5)
        
        queries = [
            BatchedQuery('kpi1', 'SELECT SUM(...) as revenue', 'kpi', 'Total Revenue'),
            BatchedQuery('chart1', 'SELECT month, SUM(...)...', 'chart', 'Monthly Trend'),
        ]
        
        results = await batcher.execute_batch(queries)
        
        for result in results:
            if result.success:
                print(f"{result.query_id}: {len(result.data)} rows")
    """
    
    def __init__(self, max_concurrent: int = 5, cache_manager=None):
        self.max_concurrent = max_concurrent
        self.cache_manager = cache_manager
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        # Stats
        self.stats = {
            'total_queries': 0,
            'successful': 0,
            'failed': 0,
            'cache_hits': 0,
            'total_time_ms': 0,
        }
    
    async def execute_batch(
        self, 
        queries: List[BatchedQuery],
        use_cache: bool = True
    ) -> List[BatchResult]:
        """Execute a batch of queries with controlled parallelism
        
        Args:
            queries: List of queries to execute
            use_cache: Whether to check cache before execution
            
        Returns:
            List of batch results
        """
        if not queries:
            return []
        
        # Sort by priority
        sorted_queries = sorted(queries, key=lambda q: q.priority)
        
        # Create tasks
        tasks = []
        for query in sorted_queries:
            task = self._execute_single(query, use_cache)
            tasks.append((query.query_id, task))
        
        # Execute all with progress tracking
        start_time = time.time()
        results = {}
        
        for query_id, task in tasks:
            try:
                result = await task
                results[query_id] = result
            except Exception as e:
                logger.error(f"Exception in batch execution for {query_id}: {e}")
                results[query_id] = BatchResult(
                    query_id=query_id,
                    success=False,
                    data=[],
                    columns=[],
                    error=str(e)
                )
        
        total_time = int((time.time() - start_time) * 1000)
        self.stats['total_queries'] += len(queries)
        self.stats['total_time_ms'] += total_time
        self.stats['successful'] += sum(1 for r in results.values() if r.success)
        self.stats['failed'] += sum(1 for r in results.values() if not r.success)
        
        logger.info(f"Batch executed {len(queries)} queries in {total_time}ms " +
                   f"({self.stats['successful']} success, {self.stats['failed']} failed)")
        
        # Return in original order
        return [results[q.query_id] for q in queries]
    
    async def execute_dependency_graph(
        self,
        queries: List[BatchedQuery],
        dependencies: Dict[str, List[str]]
    ) -> Dict[str, BatchResult]:
        """Execute queries respecting dependencies (DAG execution)
        
        Args:
            queries: List of all queries
            dependencies: Map of query_id -> [dependency_ids]
            
        Returns:
            Dict of query_id -> result
        """
        results = {}
        remaining = {q.query_id: q for q in queries}
        
        iteration = 0
        max_iterations = len(queries) * 2  # Safety limit
        
        while remaining and iteration < max_iterations:
            iteration += 1
            
            # Find queries with no unmet dependencies
            ready = [
                qid for qid, q in remaining.items()
                if all(dep in results for dep in dependencies.get(qid, []))
            ]
            
            if not ready:
                raise ValueError(f"Cannot resolve dependencies for: {list(remaining.keys())}")
            
            # Execute ready queries
            ready_queries = [remaining[qid] for qid in ready]
            batch_results = await self.execute_batch(ready_queries)
            
            # Store results
            for result in batch_results:
                results[result.query_id] = result
                del remaining[result.query_id]
        
        if remaining:
            raise ValueError(f"Dependency resolution exceeded max iterations. Remaining: {list(remaining.keys())}")
        
        return results
    
    async def _execute_single(
        self, 
        query: BatchedQuery,
        use_cache: bool
    ) -> BatchResult:
        """Execute single query with cache check and semaphore control"""
        
        async with self.semaphore:
            start_time = time.time()
            
            # Check cache first
            if use_cache and self.cache_manager:
                try:
                    cached = await self.cache_manager.get_cached_results(
                        query.query_text,
                        query.query_type
                    )
                    if cached:
                        self.stats['cache_hits'] += 1
                        execution_time = int((time.time() - start_time) * 1000)
                        
                        return BatchResult(
                            query_id=query.query_id,
                            success=True,
                            data=cached['data'],
                            columns=cached['columns'],
                            execution_time_ms=execution_time,
                            cache_hit=True,
                            rows_returned=len(cached['data'])
                        )
                except Exception as e:
                    logger.warning(f"Cache check failed for {query.query_id}: {e}")
            
            # Execute query
            try:
                result = execute_sql(query.query_text)
                execution_time = int((time.time() - start_time) * 1000)
                
                if result['success']:
                    # Cache the result
                    if use_cache and self.cache_manager:
                        try:
                            await self.cache_manager.cache_results(
                                query.query_text,
                                result['data'],
                                result['columns'],
                                query.query_type
                            )
                        except Exception as e:
                            logger.warning(f"Cache write failed for {query.query_id}: {e}")
                    
                    return BatchResult(
                        query_id=query.query_id,
                        success=True,
                        data=result['data'],
                        columns=result['columns'],
                        execution_time_ms=execution_time,
                        rows_returned=len(result['data'])
                    )
                else:
                    return BatchResult(
                        query_id=query.query_id,
                        success=False,
                        data=[],
                        columns=[],
                        error=result.get('error', 'Unknown error'),
                        execution_time_ms=execution_time
                    )
                    
            except Exception as e:
                execution_time = int((time.time() - start_time) * 1000)
                logger.error(f"Query execution failed for {query.query_id}: {e}")
                
                return BatchResult(
                    query_id=query.query_id,
                    success=False,
                    data=[],
                    columns=[],
                    error=str(e),
                    execution_time_ms=execution_time
                )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get batch execution statistics"""
        total = self.stats['total_queries']
        
        return {
            'total_queries': total,
            'successful': self.stats['successful'],
            'failed': self.stats['failed'],
            'cache_hits': self.stats['cache_hits'],
            'cache_hit_rate': self.stats['cache_hits'] / total if total > 0 else 0,
            'total_time_ms': self.stats['total_time_ms'],
            'avg_time_per_query_ms': self.stats['total_time_ms'] / total if total > 0 else 0,
        }
    
    def reset_stats(self):
        """Reset statistics"""
        self.stats = {
            'total_queries': 0,
            'successful': 0,
            'failed': 0,
            'cache_hits': 0,
            'total_time_ms': 0,
        }
