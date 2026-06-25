"""Enhanced Pipeline - Integration of new logging and intelligence with existing Claude pipeline.

Wraps the existing ClaudeReportPipeline with:
- Trace ID generation and propagation
- Signal detection on graph data
- SQL execution logging
- Performance optimizations (caching, batching)
"""

import uuid
import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from ai.claude_multi_agent import ClaudeReportPipeline
from ai.agents import AgentEventBus, LoggingAgent, AgentOrchestrator, AgentStep
from ai.intelligence import SignalDetectionEngine, DriftDetectionEngine
from ai.optimization import QueryCacheManager, QueryBatchingEngine, GraphOptimizationEngine
from db.connection import get_engine

logger = logging.getLogger(__name__)


class EnhancedReportPipeline:
    """Enhanced pipeline with logging, signals, and optimization
    
    This wraps the existing ClaudeReportPipeline with enterprise features:
    - Full traceability via trace_id
    - Automatic signal detection on all graphs
    - Query caching and batching
    - Graph data optimization
    
    Usage:
        pipeline = EnhancedReportPipeline()
        
        result = await pipeline.generate(
            question="Revenue trend last 6 months",
            filters={'date_from': '2026-01-01'}
        )
        
        # result includes trace_id for full traceability
        trace_id = result['trace_id']
    """
    
    def __init__(
        self,
        enable_logging: bool = False,  # DB logging is write-only (nothing reads it) — off by default
        enable_signals: bool = True,
        enable_caching: bool = True,
        enable_optimization: bool = True
    ):
        # Core pipeline
        self.base_pipeline = ClaudeReportPipeline()
        
        # New infrastructure
        self.event_bus = AgentEventBus()
        self.logging_agent = LoggingAgent(self.event_bus) if enable_logging else None
        
        # Intelligence engines
        self.signal_engine = SignalDetectionEngine() if enable_signals else None
        self.drift_engine = DriftDetectionEngine() if enable_signals else None
        
        # Optimization
        self.cache_manager = QueryCacheManager() if enable_caching else None
        self.batch_engine = QueryBatchingEngine(cache_manager=self.cache_manager) if enable_caching else None
        self.graph_optimizer = GraphOptimizationEngine() if enable_optimization else None
        
        # Register logging agent with event bus
        if self.logging_agent:
            self.event_bus.register_agent(self.logging_agent)
        
        logger.info(
            f"Enhanced pipeline initialized: "
            f"logging={enable_logging}, signals={enable_signals}, "
            f"caching={enable_caching}, optimization={enable_optimization}"
        )
    
    async def generate(
        self,
        question: str,
        filters: Optional[Dict] = None,
        provider: str = "claude",
        force_refresh: bool = False,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate a report with full traceability and intelligence
        
        Args:
            question: User's natural language question
            filters: Optional filters (date range, status, etc.)
            provider: LLM provider (claude, groq, etc.)
            force_refresh: Bypass cache for fresh generation
            trace_id: Optional existing trace ID (for continuing a trace)
            
        Returns:
            Enhanced report with trace_id, signals, and performance metrics
        """
        # Generate trace_id if not provided
        if not trace_id:
            trace_id = str(uuid.uuid4())
        
        pipeline_start = time.time()
        
        logger.info(f"[{trace_id[:8]}] Starting enhanced pipeline for: {question[:60]}...")
        
        # Log pipeline start
        if self.logging_agent:
            await self.logging_agent.log_pipeline_start(
                trace_id=trace_id,
                user_query=question,
                intent_detected='STANDARD_REPORT'  # Will be updated after intent detection
            )
        
        try:
            # Call base pipeline
            base_result = self._call_base_pipeline(
                question=question,
                provider=provider,
                force_refresh=force_refresh
            )
            
            # Extract report
            report = base_result.get('report', {})
            intent_mode = base_result.get('intent_mode', 'STANDARD_REPORT')
            
            # Update intent in logs
            if self.logging_agent:
                await self._update_intent(trace_id, intent_mode)
            
            # Process graphs with signal detection (non-critical — failures here
            # must not prevent the report from being returned to the user)
            try:
                enhanced_report = await self._enhance_report(
                    report=report,
                    trace_id=trace_id,
                    question=question
                )
            except Exception as enhance_err:
                logger.warning(f"[{trace_id[:8]}] Enhancement failed (non-fatal): {enhance_err}")
                enhanced_report = report
            
            # Calculate performance metrics
            total_time = time.time() - pipeline_start
            
            # Telemetry from the base Claude pipeline (token/cost/per-agent metrics)
            base_metrics = base_result.get('metrics', {})

            # Build enhanced response
            response = {
                'trace_id': trace_id,
                'mode': base_result.get('mode', 'report'),
                'intent_mode': intent_mode,
                'report': enhanced_report,
                'metrics': base_metrics,
                'applicable_filters': base_result.get('applicable_filters', []),
                'ui_instructions': base_result.get('ui_instructions', {}),
                'performance': {
                    'total_time_ms': int(total_time * 1000),
                    'pipeline_time_ms': base_metrics.get('total_time_ms'),
                    'estimated_cost_usd': base_metrics.get('estimated_cost_usd'),
                    'total_tokens': base_metrics.get('total_tokens'),
                    'cache_hit_rate_pct': base_metrics.get('cache_hit_rate_pct'),
                    'signals_enabled': self.signal_engine is not None,
                }
            }
            
            # Add drift context if present
            if 'drift_context' in base_result:
                response['drift_context'] = base_result['drift_context']
            
            # Log pipeline completion
            if self.logging_agent:
                from ai.agents import AgentContext
                context = AgentContext(
                    trace_id=trace_id,
                    query_context={'question': question, 'filters': filters},
                    signal_context={'count': len(enhanced_report.get('_signals', []))},
                    timestamp=datetime.now()
                )
                await self.logging_agent.log_pipeline_complete(trace_id, context)
            
            logger.info(f"[{trace_id[:8]}] Pipeline completed in {total_time:.2f}s")
            
            return response
            
        except Exception as e:
            logger.error(f"[{trace_id[:8]}] Pipeline failed: {e}")
            
            if self.logging_agent:
                await self.logging_agent.log_agent_failure(
                    trace_id=trace_id,
                    agent_name='enhanced_pipeline',
                    error=str(e),
                    retry_count=0
                )
            
            return {
                'trace_id': trace_id,
                'status': 'failed',
                'error': str(e),
                'report': None
            }
    
    def _call_base_pipeline(
        self,
        question: str,
        provider: str,
        force_refresh: bool
    ) -> Dict[str, Any]:
        """Call the base Claude pipeline (synchronous wrapper)"""
        
        # The base pipeline is synchronous, so we call it directly
        # In production, you might want to run this in a thread pool
        return self.base_pipeline.generate(
            question=question,
            force_refresh=force_refresh
        )
    
    async def _enhance_report(
        self,
        report: Dict[str, Any],
        trace_id: str,
        question: str
    ) -> Dict[str, Any]:
        """Enhance report with signals and optimization"""
        
        enhanced = {**report}
        all_signals = []
        
        # Process charts
        if 'charts' in report and self.signal_engine:
            enhanced_charts = []
            
            for idx, chart in enumerate(report['charts']):
                graph_id = chart.get('id', f'chart_{idx+1}')
                
                # Get chart data
                data = chart.get('data', [])
                chart_type = chart.get('type', 'bar')
                
                if not data:
                    enhanced_charts.append(chart)
                    continue
                
                # Detect columns
                first_row = data[0] if data else {}
                keys = list(first_row.keys())
                label_col = keys[0] if keys else 'label'
                value_col = keys[1] if len(keys) > 1 else 'value'
                
                # Run signal detection
                signals = self.signal_engine.analyze(
                    data=data,
                    value_column=value_col,
                    label_column=label_col,
                    context={
                        'chart_type': chart_type,
                        'chart_title': chart.get('title', ''),
                        'metric_name': value_col,
                        'currency': 'INR'
                    }
                )
                
                # Log SQL mapping with signals
                if self.logging_agent and 'sql' in chart:
                    await self.logging_agent.log_graph_sql_mapping(
                        trace_id=trace_id,
                        graph_id=graph_id,
                        graph_title=chart.get('title', ''),
                        graph_type=chart_type,
                        sql_query=chart.get('sql', ''),
                        selection_reason=chart.get('_selection_reason', 'Standard chart for data type'),
                        selection_confidence=chart.get('_selection_confidence', 0.8),
                        signals_found=[{
                            'type': s.signal_type.value,
                            'severity': s.severity.value,
                            'location': s.location,
                            'variance_pct': s.variance_pct
                        } for s in signals],
                        drifts_found=[]
                    )
                
                # Log signals
                for signal in signals:
                    all_signals.append({
                        'graph_id': graph_id,
                        'signal_type': signal.signal_type.value,
                        'severity': signal.severity.value,
                        'location': signal.location,
                        'description': signal.description,
                        'what_changed': signal.what_changed,
                        'why_changed': signal.why_changed,
                        'impact': signal.impact_assessment,
                        'action': signal.recommended_action
                    })
                    
                    if self.logging_agent:
                        await self.logging_agent.log_signal(
                            trace_id=trace_id,
                            signal_data={
                                'signal_name': f"{signal.signal_type.value.upper()} in {chart.get('title', 'Chart')}",
                                'signal_domain': 'ANALYTICS',
                                'severity': signal.severity.value,
                                'primary_metric': value_col,
                                'metric_value': signal.value,
                                'baseline_value': signal.baseline,
                                'variance_pct': signal.variance_pct,
                                'trigger_condition': signal.description,
                                'what_changed': signal.what_changed,
                                'impact_amount': abs(signal.value - signal.baseline) if signal.baseline else 0
                            }
                        )
                
                # Enhance chart with signal info
                enhanced_chart = {
                    **chart,
                    '_signals': [
                        {
                            'type': s.signal_type.value,
                            'severity': s.severity.value,
                            'location': s.location,
                            'description': s.description,
                            'confidence': round(s.confidence, 2)
                        }
                        for s in signals
                    ],
                    '_signal_count': len(signals)
                }
                
                enhanced_charts.append(enhanced_chart)
            
            enhanced['charts'] = enhanced_charts
        
        # Optimize graph data if needed
        if self.graph_optimizer and 'charts' in enhanced:
            enhanced['charts'] = self.graph_optimizer.optimize_all_graphs(
                enhanced['charts'],
                max_points=100
            )
        
        # Store all signals at report level
        enhanced['_signals'] = all_signals
        enhanced['_signal_summary'] = self._summarize_signals(all_signals)
        
        return enhanced
    
    def _summarize_signals(self, signals: List[Dict]) -> Dict[str, Any]:
        """Create a summary of detected signals"""
        
        if not signals:
            return {'count': 0, 'has_critical': False}
        
        by_severity = {}
        for s in signals:
            sev = s.get('severity', 'low')
            by_severity[sev] = by_severity.get(sev, 0) + 1
        
        return {
            'count': len(signals),
            'has_critical': by_severity.get('critical', 0) > 0,
            'has_high': by_severity.get('high', 0) > 0,
            'by_severity': by_severity,
            'top_signals': signals[:3]  # Top 3 by severity
        }
    
    async def _update_intent(self, trace_id: str, intent_mode: str):
        """Update intent in existing log entries"""
        
        # This would update the intent_detected field in logs
        # Implementation depends on your logging strategy
        pass
    
    async def get_trace(self, trace_id: str) -> Dict[str, Any]:
        """Get full execution trace for a report
        
        Args:
            trace_id: The trace ID to retrieve
            
        Returns:
            Complete trace including all agent executions, SQL queries, signals
        """
        if not self.logging_agent:
            return {'error': 'Logging not enabled'}
        
        # Get all logs
        logs = await self.logging_agent.get_logs(trace_id)
        sql_mappings = await self.logging_agent.get_sql_mappings(trace_id)
        signals = await self.logging_agent.get_signals(trace_id)
        
        return {
            'trace_id': trace_id,
            'execution_summary': {
                'total_agents': len(logs),
                'total_sql_queries': len(sql_mappings),
                'total_signals': len(signals),
                'status': 'completed' if any(l.get('status') == 'success' for l in logs) else 'failed'
            },
            'agents': logs,
            'sql_mappings': sql_mappings,
            'signals': signals
        }
    
    async def get_active_signals(self, limit: int = 50) -> List[Dict]:
        """Get active signals requiring attention"""
        
        if not self.logging_agent:
            return []
        
        try:
            from sqlalchemy import text
            
            with get_engine().connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT * FROM signal_detection_logs 
                        WHERE status IN ('NEW', 'INVESTIGATING')
                        ORDER BY detection_timestamp DESC
                        LIMIT :limit
                    """),
                    {'limit': limit}
                )
                
                return [dict(row._mapping) for row in result]
        except Exception as e:
            logger.error(f"Failed to get active signals: {e}")
            return []
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get pipeline performance statistics"""
        
        stats = {
            'cache_enabled': self.cache_manager is not None,
            'signals_enabled': self.signal_engine is not None,
        }
        
        if self.cache_manager:
            stats['cache'] = self.cache_manager.get_cache_stats()
        
        if self.batch_engine:
            stats['batch'] = self.batch_engine.get_stats()
        
        return stats
