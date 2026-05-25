"""Enhanced API routes for traceability and intelligence.

Adds new endpoints:
- /report/enhanced - Generate report with full traceability
- /report/trace/{trace_id} - Get execution trace
- /signals/active - Get active signals
- /admin/cache/invalidate - Cache management
"""

from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
import logging

logger = logging.getLogger("api_enhanced")

# Create router
router = APIRouter(prefix="/api/v2", tags=["enhanced"])


# ── Request/Response Models ──────────────────────────────────────────────

class EnhancedReportRequest(BaseModel):
    question: str
    provider: str = "claude"
    force_refresh: bool = False
    # Filters
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    aggregation: Optional[str] = None
    category: Optional[str] = None
    customer: Optional[str] = None
    status: Optional[str] = None
    product: Optional[str] = None


class EnhancedReportResponse(BaseModel):
    trace_id: str
    mode: str
    intent_mode: str
    report: dict
    applicable_filters: list
    ui_instructions: dict
    performance: dict
    drift_context: Optional[dict] = None


class TraceResponse(BaseModel):
    trace_id: str
    execution_summary: dict
    agents: list
    sql_mappings: list
    signals: list


class SignalsResponse(BaseModel):
    signals: list
    count: int
    summary: dict


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/report/enhanced", response_model=EnhancedReportResponse)
async def enhanced_report_endpoint(req: EnhancedReportRequest):
    """Generate a report with full traceability and signal detection.
    
    This endpoint provides:
    - trace_id for complete execution traceability
    - Automatic signal detection on all graphs
    - Performance metrics
    - Full SQL-to-graph mappings
    """
    from ai.enhanced_pipeline import EnhancedReportPipeline
    
    logger.info(
        "ENHANCED REPORT request | provider=%s | question=%s",
        req.provider, req.question
    )
    
    # Build filter context
    filters = []
    if req.date_from:
        filters.append(f"Date from: {req.date_from}")
    if req.date_to:
        filters.append(f"Date to: {req.date_to}")
    if req.aggregation:
        filters.append(f"Aggregation: {req.aggregation}")
    if req.category:
        filters.append(f"Category: {req.category}")
    if req.customer:
        filters.append(f"Customer: {req.customer}")
    if req.status:
        filters.append(f"Status: {req.status}")
    if req.product:
        filters.append(f"Product: {req.product}")
    
    filter_ctx = ""
    if filters:
        filter_ctx = "\n[ACTIVE FILTERS: " + "; ".join(filters) + "]"
    
    question_with_filters = req.question + filter_ctx
    
    # Initialize enhanced pipeline
    pipeline = EnhancedReportPipeline(
        enable_logging=True,
        enable_signals=True,
        enable_caching=True,
        enable_optimization=True
    )
    
    # Generate report
    result = await pipeline.generate(
        question=question_with_filters,
        provider=req.provider,
        force_refresh=req.force_refresh
    )
    
    if result.get('status') == 'failed':
        raise HTTPException(status_code=500, detail=result.get('error', 'Report generation failed'))
    
    return EnhancedReportResponse(**result)


@router.get("/report/trace/{trace_id}", response_model=TraceResponse)
async def get_trace_endpoint(trace_id: str):
    """Get complete execution trace for a report.
    
    Returns:
    - All agent execution steps
    - SQL query mappings
    - Detected signals
    - Performance data
    """
    from ai.enhanced_pipeline import EnhancedReportPipeline
    
    logger.info("GET TRACE request | trace_id=%s", trace_id)
    
    pipeline = EnhancedReportPipeline()
    trace = await pipeline.get_trace(trace_id)
    
    if not trace or 'error' in trace:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    
    return TraceResponse(**trace)


@router.get("/signals/active", response_model=SignalsResponse)
async def get_active_signals_endpoint(limit: int = 50):
    """Get active signals requiring attention.
    
    Returns signals that are NEW or INVESTIGATING status.
    """
    from ai.enhanced_pipeline import EnhancedReportPipeline
    
    logger.info("GET ACTIVE SIGNALS request | limit=%d", limit)
    
    pipeline = EnhancedReportPipeline()
    signals = await pipeline.get_active_signals(limit=limit)
    
    # Summarize by severity
    by_severity = {}
    for s in signals:
        sev = s.get('severity', 'low')
        by_severity[sev] = by_severity.get(sev, 0) + 1
    
    return SignalsResponse(
        signals=signals,
        count=len(signals),
        summary={
            'by_severity': by_severity,
            'has_critical': by_severity.get('critical', 0) > 0
        }
    )


@router.get("/drifts/recent")
async def get_recent_drifts_endpoint(limit: int = 20):
    """Get recent drift detections.
    
    Returns recent data/metric/trend/distribution drifts.
    """
    from db.connection import get_engine
    from sqlalchemy import text
    
    logger.info("GET RECENT DRIFTS request | limit=%d", limit)
    
    try:
        with get_engine().connect() as conn:
            result = conn.execute(
                text("""
                    SELECT * FROM drift_detection_logs 
                    ORDER BY detection_timestamp DESC
                    LIMIT :limit
                """),
                {'limit': limit}
            )
            
            drifts = [dict(row._mapping) for row in result]
            
            return {
                'drifts': drifts,
                'count': len(drifts)
            }
    except Exception as e:
        logger.error(f"Failed to get recent drifts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin/cache/invalidate")
async def invalidate_cache_endpoint(pattern: Optional[str] = None):
    """Invalidate query cache.
    
    Args:
        pattern: Optional pattern to match queries for selective invalidation
    """
    from ai.optimization import QueryCacheManager
    
    logger.info("CACHE INVALIDATE request | pattern=%s", pattern or "all")
    
    cache_manager = QueryCacheManager()
    await cache_manager.invalidate_cache(pattern=pattern)
    
    return {
        'status': 'success',
        'message': f"Cache invalidated" + (f" matching pattern: {pattern}" if pattern else " (all)")
    }


@router.get("/admin/performance/metrics")
async def get_performance_metrics_endpoint():
    """Get pipeline performance metrics."""
    from ai.enhanced_pipeline import EnhancedReportPipeline
    
    logger.info("GET PERFORMANCE METRICS request")
    
    pipeline = EnhancedReportPipeline()
    stats = pipeline.get_performance_stats()
    
    return {
        'performance': stats,
        'timestamp': __import__('datetime').datetime.now().isoformat()
    }


@router.get("/health/enhanced")
async def health_check_enhanced():
    """Enhanced health check including new infrastructure."""
    from ai.agents import AgentEventBus, LoggingAgent
    from ai.intelligence import SignalDetectionEngine
    from ai.optimization import QueryCacheManager
    from db.connection import get_engine
    
    status = {
        'status': 'healthy',
        'components': {}
    }
    
    # Check database
    try:
        from sqlalchemy import text as _text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(_text("SELECT 1"))
        status['components']['database'] = 'connected'
    except Exception as e:
        status['components']['database'] = f'error: {e}'
        status['status'] = 'degraded'
    
    # Check logging tables
    try:
        from sqlalchemy import text as _text
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(_text("SELECT COUNT(*) FROM agent_execution_logs"))
            count = result.scalar()
        status['components']['logging'] = f'ok ({count} logs)'
    except Exception as e:
        status['components']['logging'] = f'error: {e}'
    
    # Check agent framework
    try:
        bus = AgentEventBus()
        status['components']['agent_bus'] = 'initialized'
    except Exception as e:
        status['components']['agent_bus'] = f'error: {e}'
    
    # Check signal engine
    try:
        engine = SignalDetectionEngine()
        status['components']['signal_detection'] = 'initialized'
    except Exception as e:
        status['components']['signal_detection'] = f'error: {e}'
    
    return status
