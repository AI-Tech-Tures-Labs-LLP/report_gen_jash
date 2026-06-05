"""Performance Optimization Layer.

Provides:
- Query caching (L1/L2/Materialized Views)
- Query batching and parallel execution
- LLM context compression
- Graph data optimization
- Index recommendations
"""

from .query_cache import QueryCacheManager
from .query_batcher import QueryBatchingEngine, BatchedQuery, BatchResult
from .context_compression import LLMContextCompressor
from .graph_optimization import GraphOptimizationEngine

__all__ = [
    'QueryCacheManager',
    'QueryBatchingEngine',
    'BatchedQuery',
    'BatchResult',
    'LLMContextCompressor',
    'GraphOptimizationEngine',
]
