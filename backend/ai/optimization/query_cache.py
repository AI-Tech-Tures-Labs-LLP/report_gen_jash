"""Multi-tier Query Caching: Memory → Redis → Materialized Views.

Provides 3 levels of caching:
- L1: In-memory LRU cache (fastest, limited size)
- L2: Redis cache (distributed, survives restart)
- L3: Materialized Views (PostgreSQL, for common patterns)
"""

import hashlib
import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from dataclasses import dataclass
import asyncio
import logging
from db.connection import get_engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


@dataclass
class CachedQuery:
    """Cached query entry"""
    query_hash: str
    query_text: str
    results: List[Dict]
    columns: List[str]
    cached_at: datetime
    ttl_seconds: int
    hit_count: int = 0
    query_type: str = 'unknown'
    
    def is_expired(self) -> bool:
        """Check if cache entry has expired"""
        return datetime.now() > self.cached_at + timedelta(seconds=self.ttl_seconds)


class QueryCacheManager:
    """Multi-tier query cache manager
    
    Usage:
        cache = QueryCacheManager()
        
        # Try to get cached results
        cached = await cache.get_cached_results(query, 'kpi')
        if cached:
            return cached['data']
        
        # Execute and cache
        results = execute_sql(query)
        await cache.cache_results(query, results, columns, 'kpi')
    """
    
    def __init__(self, redis_client=None, db_engine=None):
        self.memory_cache: Dict[str, CachedQuery] = {}
        self.redis = redis_client
        self._engine = db_engine
        self._lock = asyncio.Lock()
        
        # TTL configuration by query type
        self.ttl_config = {
            'kpi': 300,        # 5 minutes for KPIs
            'chart': 600,      # 10 minutes for charts
            'table': 120,      # 2 minutes for detail tables
            'drift': 1800,     # 30 minutes for drift analysis
            'default': 300,
        }
        
        # Memory cache limits
        self.max_memory_entries = 1000
        
        # Stats
        self.stats = {
            'l1_hits': 0,
            'l2_hits': 0,
            'l3_hits': 0,
            'misses': 0,
        }
    
    def _get_engine(self):
        """Lazy load database engine"""
        if self._engine is None:
            self._engine = get_engine()
        return self._engine
    
    def _hash_query(self, query: str, context: Dict = None) -> str:
        """Generate deterministic hash for query + context"""
        hash_input = query.strip().lower()
        if context:
            # Only hash relevant context fields
            relevant = {k: v for k, v in context.items() 
                       if k in ['date_from', 'date_to', 'filters', 'aggregation']}
            hash_input += json.dumps(relevant, sort_keys=True, default=str)
        return hashlib.sha256(hash_input.encode()).hexdigest()[:32]
    
    async def get_cached_results(
        self, 
        query: str, 
        query_type: str = 'default',
        context: Dict = None
    ) -> Optional[Dict[str, Any]]:
        """Get cached results from any cache tier
        
        Args:
            query: SQL query text
            query_type: Type of query (kpi, chart, table, drift)
            context: Query context for cache key
            
        Returns:
            Cached results dict or None
        """
        query_hash = self._hash_query(query, context)
        
        # L1: Memory cache
        async with self._lock:
            cached = self.memory_cache.get(query_hash)
            if cached and not cached.is_expired():
                cached.hit_count += 1
                self.stats['l1_hits'] += 1
                logger.debug(f"L1 cache HIT: {query_hash[:8]}... ({cached.hit_count} hits)")
                return {
                    'data': cached.results,
                    'columns': cached.columns,
                    'source': 'memory_cache',
                    'cache_hit': True,
                    'cached_at': cached.cached_at.isoformat(),
                }
            
            # Remove expired entry
            if cached and cached.is_expired():
                del self.memory_cache[query_hash]
        
        # L2: Redis cache (if available)
        if self.redis:
            try:
                redis_key = f"query_cache:{query_hash}"
                cached_data = await self.redis.get(redis_key)
                if cached_data:
                    data = json.loads(cached_data)
                    # Populate memory cache (L1)
                    await self._set_memory_cache(
                        query_hash, query, data, 
                        self.ttl_config.get(query_type, 300),
                        query_type
                    )
                    self.stats['l2_hits'] += 1
                    logger.debug(f"L2 cache HIT: {query_hash[:8]}...")
                    return {
                        **data, 
                        'source': 'redis_cache', 
                        'cache_hit': True
                    }
            except Exception as e:
                logger.warning(f"Redis cache read failed: {e}")
        
        # L3: Materialized view check (for common patterns)
        mv_result = await self._check_materialized_view(query, query_type)
        if mv_result:
            self.stats['l3_hits'] += 1
            logger.debug(f"L3 (MV) cache HIT: {query_hash[:8]}...")
            return {**mv_result, 'source': 'materialized_view', 'cache_hit': True}
        
        # Cache miss
        self.stats['misses'] += 1
        logger.debug(f"Cache MISS: {query_hash[:8]}...")
        return None
    
    async def cache_results(
        self,
        query: str,
        results: List[Dict],
        columns: List[str],
        query_type: str = 'default',
        context: Dict = None
    ) -> str:
        """Cache query results
        
        Args:
            query: SQL query text
            results: Query results to cache
            columns: Column names
            query_type: Type of query
            context: Query context
            
        Returns:
            Cache key (query hash)
        """
        query_hash = self._hash_query(query, context)
        ttl = self.ttl_config.get(query_type, 300)
        
        data = {
            'data': results,
            'columns': columns,
        }
        
        # L1: Memory cache
        await self._set_memory_cache(query_hash, query, data, ttl, query_type)
        
        # L2: Redis cache
        if self.redis:
            try:
                redis_key = f"query_cache:{query_hash}"
                await self.redis.setex(
                    redis_key,
                    ttl,
                    json.dumps(data, default=str)
                )
            except Exception as e:
                logger.warning(f"Redis cache write failed: {e}")
        
        # Update cache metadata in database
        await self._update_cache_metadata(query_hash, query, query_type, len(results))
        
        return query_hash
    
    async def _set_memory_cache(
        self, 
        query_hash: str, 
        query: str, 
        data: Dict,
        ttl: int,
        query_type: str = 'unknown'
    ):
        """Set entry in memory cache with LRU eviction"""
        
        async with self._lock:
            # LRU eviction if cache too large
            if len(self.memory_cache) >= self.max_memory_entries:
                # Remove least recently used (lowest hit count)
                lru_key = min(
                    self.memory_cache.keys(), 
                    key=lambda k: self.memory_cache[k].hit_count
                )
                del self.memory_cache[lru_key]
                logger.debug(f"LRU evicted: {lru_key[:8]}...")
            
            self.memory_cache[query_hash] = CachedQuery(
                query_hash=query_hash,
                query_text=query[:500],  # Truncate for memory
                results=data['data'],
                columns=data['columns'],
                cached_at=datetime.now(),
                ttl_seconds=ttl,
                hit_count=0,
                query_type=query_type
            )
    
    async def _check_materialized_view(
        self, 
        query: str, 
        query_type: str
    ) -> Optional[Dict]:
        """Check if query matches a materialized view pattern"""
        
        import re
        
        # Define MV patterns for common queries
        mv_patterns = {
            'mv_monthly_revenue': {
                'pattern': r"SELECT.*DATE_TRUNC\s*\(\s*'month'.*SUM.*total_amount",
                'columns': ['month', 'revenue']
            },
            'mv_top_customers': {
                'pattern': r"SELECT.*customer.*SUM.*ORDER\s+BY.*DESC",
                'columns': ['customer_name', 'total_revenue']
            },
            'mv_order_status': {
                'pattern': r"SELECT.*status.*COUNT.*GROUP\s+BY.*status",
                'columns': ['status', 'count']
            },
            'mv_product_sales': {
                'pattern': r"SELECT.*product.*SUM.*quantity",
                'columns': ['product_name', 'total_quantity', 'total_revenue']
            },
        }
        
        for mv_name, config in mv_patterns.items():
            if re.search(config['pattern'], query, re.IGNORECASE):
                try:
                    with self._get_engine().connect() as conn:
                        # Check if MV exists and is fresh
                        result = conn.execute(
                            text("""
                                SELECT * FROM pg_matviews 
                                WHERE matviewname = :mv_name
                                AND last_refresh > NOW() - INTERVAL '10 minutes'
                            """),
                            {'mv_name': mv_name}
                        )
                        
                        if result.fetchone():
                            # Query the MV
                            data_result = conn.execute(
                                text(f"SELECT * FROM {mv_name} LIMIT 100")
                            )
                            rows = [dict(row._mapping) for row in data_result]
                            
                            return {
                                'data': rows,
                                'columns': config['columns'],
                            }
                except Exception as e:
                    logger.debug(f"MV check failed for {mv_name}: {e}")
        
        return None
    
    async def _update_cache_metadata(
        self,
        query_hash: str,
        query: str,
        query_type: str,
        row_count: int
    ):
        """Update cache metadata in database"""
        
        try:
            # Extract table names from query
            import re
            tables = re.findall(r'FROM\s+(\w+)', query, re.IGNORECASE)
            tables += re.findall(r'JOIN\s+(\w+)', query, re.IGNORECASE)
            
            with self._get_engine().connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO query_cache_metadata (
                            query_hash, query_text, query_type,
                            table_names, last_used_at, use_count
                        ) VALUES (
                            :hash, :query, :type,
                            :tables, NOW(), 1
                        )
                        ON CONFLICT (query_hash) DO UPDATE SET
                            last_used_at = NOW(),
                            use_count = query_cache_metadata.use_count + 1
                    """),
                    {
                        'hash': query_hash,
                        'query': query[:2000],
                        'type': query_type,
                        'tables': list(set(tables))
                    }
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to update cache metadata: {e}")
    
    async def invalidate_cache(
        self,
        pattern: str = None,
        table_names: List[str] = None
    ):
        """Invalidate cache entries
        
        Args:
            pattern: Optional pattern to match queries
            table_names: Invalidate queries touching these tables
        """
        # Clear memory cache
        if pattern or table_names:
            # Selective clear
            async with self._lock:
                to_remove = []
                for key, cached in self.memory_cache.items():
                    if pattern and pattern in cached.query_text:
                        to_remove.append(key)
                    # Note: table-based invalidation would need metadata
                
                for key in to_remove:
                    del self.memory_cache[key]
                    logger.info(f"Invalidated from memory: {key[:8]}...")
        else:
            # Full clear
            async with self._lock:
                count = len(self.memory_cache)
                self.memory_cache.clear()
                logger.info(f"Cleared {count} entries from memory cache")
        
        # Clear Redis if available
        if self.redis:
            try:
                if pattern:
                    # Pattern-based invalidation
                    keys = await self.redis.keys(f"query_cache:*{pattern}*")
                    if keys:
                        await self.redis.delete(*keys)
                        logger.info(f"Invalidated {len(keys)} keys from Redis")
                else:
                    # Full clear
                    await self.redis.delete(*await self.redis.keys("query_cache:*"))
                    logger.info("Cleared Redis cache")
            except Exception as e:
                logger.warning(f"Redis invalidation failed: {e}")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total_hits = self.stats['l1_hits'] + self.stats['l2_hits'] + self.stats['l3_hits']
        total_requests = total_hits + self.stats['misses']
        
        hit_rate = total_hits / total_requests if total_requests > 0 else 0
        
        return {
            'l1_memory_hits': self.stats['l1_hits'],
            'l2_redis_hits': self.stats['l2_hits'],
            'l3_mv_hits': self.stats['l3_hits'],
            'misses': self.stats['misses'],
            'total_hits': total_hits,
            'total_requests': total_requests,
            'hit_rate': hit_rate,
            'memory_cache_size': len(self.memory_cache),
            'memory_cache_limit': self.max_memory_entries,
        }
    
    def reset_stats(self):
        """Reset cache statistics"""
        self.stats = {
            'l1_hits': 0,
            'l2_hits': 0,
            'l3_hits': 0,
            'misses': 0,
        }
