"""Logging Agent for persistent traceability and audit logging.

Persists all agent activities to PostgreSQL for:
- Complete execution traceability
- Performance analysis
- Debugging and troubleshooting
- Compliance and audit requirements
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import json
import logging

from .base_agent import BaseAgent, AgentContext
from db.connection import get_engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


def _num(v, default=0.0):
    """Coerce numpy/Decimal/None to a plain Python float for psycopg2.

    Signal/drift values arrive as numpy.float64 (from the detectors), which
    psycopg2 cannot adapt — it was emitting them as literal `np.float64(...)`
    text into the SQL, causing 'schema "np" does not exist'. Plain float fixes it.
    """
    try:
        return float(v) if v is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


class LoggingAgent(BaseAgent):
    """Agent responsible for logging all pipeline activities
    
    Usage:
        logging_agent = LoggingAgent(event_bus)
        
        # Log pipeline start
        await logging_agent.log_pipeline_start(trace_id, user_query)
        
        # Log agent execution
        await logging_agent.log_agent_start(trace_id, agent_name, step)
        await logging_agent.log_agent_complete(trace_id, agent_name, result, duration)
        
        # Log SQL execution
        await logging_agent.log_sql_execution(trace_id, query, results, duration)
        
        # Log signals
        await logging_agent.log_signal(trace_id, signal_data)
    """
    
    def __init__(self, event_bus=None):
        super().__init__('logging_agent', event_bus)
        self._engine = None
    
    def _get_engine(self):
        """Lazy load database engine"""
        if self._engine is None:
            self._engine = get_engine()
        return self._engine
    
    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Logging agent doesn't execute in the traditional sense"""
        return {'status': 'success', 'logs_written': 0}
    
    async def log_pipeline_start(
        self, 
        trace_id: str, 
        user_query: str,
        intent_detected: str = 'STANDARD_REPORT',
        intent_confidence: float = 1.0
    ):
        """Log pipeline initiation
        
        Args:
            trace_id: Unique trace identifier
            user_query: Original user query
            intent_detected: Detected intent type
            intent_confidence: Confidence score for intent
        """
        try:
            with self._get_engine().connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO audit_trail (
                            trace_id, event_type, event_severity,
                            agent_name, event_description, event_data
                        ) VALUES (
                            :trace_id, 'pipeline_start', 'info',
                            'orchestrator', 'Pipeline initiated',
                            CAST(:event_data AS jsonb)
                        )
                    """),
                    {
                        'trace_id': trace_id,
                        'event_data': json.dumps({
                            'user_query': user_query,
                            'intent_detected': intent_detected,
                            'intent_confidence': intent_confidence,
                            'timestamp': datetime.now().isoformat()
                        })
                    }
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to log pipeline start: {e}")
    
    async def log_agent_start(
        self,
        trace_id: str,
        agent_name: str,
        step_number: int,
        input_data: Optional[Dict] = None
    ):
        """Log agent execution start
        
        Args:
            trace_id: Trace identifier
            agent_name: Name of the agent
            step_number: Step number in pipeline
            input_data: Input to the agent (optional)
        """
        try:
            with self._get_engine().connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO agent_execution_logs (
                            trace_id, correlation_id, user_query, intent_detected,
                            agent_name, agent_step, agent_start_time, status,
                            input_context
                        ) VALUES (
                            :trace_id, :correlation_id, :user_query, :intent_detected,
                            :agent_name, :agent_step, NOW(), 'processing',
                            CAST(:input_context AS jsonb)
                        )
                    """),
                    {
                        'trace_id': trace_id,
                        'correlation_id': trace_id,
                        'user_query': input_data.get('question', '') if input_data else '',
                        'intent_detected': input_data.get('intent_mode', 'STANDARD_REPORT') if input_data else 'STANDARD_REPORT',
                        'agent_name': agent_name,
                        'agent_step': step_number,
                        'input_context': json.dumps(input_data) if input_data else '{}'
                    }
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to log agent start: {e}")
    
    async def log_agent_complete(
        self,
        trace_id: str,
        agent_name: str,
        result: Dict[str, Any],
        duration_seconds: float,
        attempt: int = 0
    ):
        """Log successful agent completion
        
        Args:
            trace_id: Trace identifier
            agent_name: Name of the agent
            result: Agent execution result
            duration_seconds: Execution duration
            attempt: Retry attempt number
        """
        try:
            with self._get_engine().connect() as conn:
                # Update the existing log entry
                conn.execute(
                    text("""
                        UPDATE agent_execution_logs
                        SET agent_end_time = NOW(),
                            agent_duration_ms = :duration_ms,
                            status = 'success',
                            output_result = CAST(:output_result AS jsonb),
                            retry_count = :attempt
                        WHERE trace_id = :trace_id 
                          AND agent_name = :agent_name
                          AND status = 'processing'
                    """),
                    {
                        'trace_id': trace_id,
                        'agent_name': agent_name,
                        'duration_ms': int(duration_seconds * 1000),
                        'output_result': json.dumps(result, default=str),
                        'attempt': attempt
                    }
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to log agent completion: {e}")
    
    async def log_agent_failure(
        self,
        trace_id: str,
        agent_name: str,
        error: str,
        retry_count: int,
        duration_seconds: float = 0
    ):
        """Log agent execution failure
        
        Args:
            trace_id: Trace identifier
            agent_name: Name of the agent
            error: Error message
            retry_count: Number of retries attempted
            duration_seconds: Execution duration
        """
        try:
            with self._get_engine().connect() as conn:
                conn.execute(
                    text("""
                        UPDATE agent_execution_logs
                        SET agent_end_time = NOW(),
                            agent_duration_ms = :duration_ms,
                            status = 'failed',
                            error_message = :error,
                            retry_count = :retry_count
                        WHERE trace_id = :trace_id 
                          AND agent_name = :agent_name
                    """),
                    {
                        'trace_id': trace_id,
                        'agent_name': agent_name,
                        'duration_ms': int(duration_seconds * 1000),
                        'error': error,
                        'retry_count': retry_count
                    }
                )
                
                # Also log to audit trail
                conn.execute(
                    text("""
                        INSERT INTO audit_trail (
                            trace_id, event_type, event_severity,
                            agent_name, event_description, event_data
                        ) VALUES (
                            :trace_id, 'agent_failure', 'error',
                            :agent_name, :description,
                            CAST(:event_data AS jsonb)
                        )
                    """),
                    {
                        'trace_id': trace_id,
                        'agent_name': agent_name,
                        'description': f"Agent {agent_name} failed after {retry_count} retries",
                        'event_data': json.dumps({
                            'error': error,
                            'retry_count': retry_count,
                            'timestamp': datetime.now().isoformat()
                        })
                    }
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to log agent failure: {e}")
    
    async def log_sql_execution(
        self,
        trace_id: str,
        query: str,
        query_type: str,
        results: Dict[str, Any],
        duration_ms: int,
        cache_hit: bool = False
    ):
        """Log SQL query execution
        
        Args:
            trace_id: Trace identifier
            query: SQL query text
            query_type: Type of query (kpi, chart, table, etc.)
            results: Execution results
            duration_ms: Execution duration in milliseconds
            cache_hit: Whether result was from cache
        """
        import hashlib
        
        try:
            query_hash = hashlib.sha256(query.encode()).hexdigest()[:32]
            
            with self._get_engine().connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO sql_execution_logs (
                            trace_id, query_hash, query_text, query_type,
                            execution_start, execution_end, execution_duration_ms,
                            rows_returned, columns_returned, validation_passed,
                            cache_hit
                        ) VALUES (
                            :trace_id, :query_hash, :query_text, :query_type,
                            NOW() - INTERVAL '1 millisecond' * :duration_ms, NOW(), :duration_ms,
                            :rows_returned, :columns_returned, :validation_passed,
                            :cache_hit
                        )
                    """),
                    {
                        'trace_id': trace_id,
                        'query_hash': query_hash,
                        'query_text': query[:2000],  # Truncate if too long
                        'query_type': query_type,
                        'duration_ms': duration_ms,
                        'rows_returned': len(results.get('data', [])),
                        'columns_returned': results.get('columns', []),
                        'validation_passed': results.get('success', False),
                        'cache_hit': cache_hit
                    }
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to log SQL execution: {e}")
    
    async def log_graph_sql_mapping(
        self,
        trace_id: str,
        graph_id: str,
        graph_title: str,
        graph_type: str,
        sql_query: str,
        selection_reason: str,
        selection_confidence: float,
        signals_found: Optional[List[Dict]] = None,
        drifts_found: Optional[List[Dict]] = None
    ):
        """Log mapping between graph and SQL query
        
        Args:
            trace_id: Trace identifier
            graph_id: Graph identifier (chart_1, chart_2, etc.)
            graph_title: Graph title
            graph_type: Graph type (line, bar, pie, etc.)
            sql_query: SQL query used for the graph
            selection_reason: Why this graph type was selected
            selection_confidence: Confidence in selection
            signals_found: Signals detected in this graph
            drifts_found: Drifts detected in this graph
        """
        import hashlib
        
        try:
            query_hash = hashlib.sha256(sql_query.encode()).hexdigest()[:64]
            
            with self._get_engine().connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO graph_sql_mappings (
                            trace_id, graph_id, graph_title, graph_type,
                            sql_query_hash, sql_query, sql_validated,
                            graph_selection_reason, graph_selection_confidence,
                            signals_found, drifts_found
                        ) VALUES (
                            :trace_id, :graph_id, :graph_title, :graph_type,
                            :query_hash, :sql_query, true,
                            :selection_reason, :selection_confidence,
                            CAST(:signals_found AS jsonb), CAST(:drifts_found AS jsonb)
                        )
                    """),
                    {
                        'trace_id': trace_id,
                        'graph_id': graph_id,
                        'graph_title': graph_title[:255],
                        'graph_type': graph_type,
                        'query_hash': query_hash,
                        'sql_query': sql_query[:2000],
                        'selection_reason': selection_reason[:1000],
                        'selection_confidence': selection_confidence,
                        'signals_found': json.dumps(signals_found or []),
                        'drifts_found': json.dumps(drifts_found or [])
                    }
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to log graph-SQL mapping: {e}")
    
    async def log_signal(
        self,
        trace_id: str,
        signal_data: Dict[str, Any]
    ):
        """Log detected signal
        
        Args:
            trace_id: Trace identifier
            signal_data: Signal detection results
        """
        try:
            with self._get_engine().connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO signal_detection_logs (
                            trace_id, signal_library_id, signal_name,
                            signal_domain, signal_category, trigger_condition,
                            primary_metric, metric_value, baseline_value,
                            variance_pct, severity, severity_score,
                            impact_amount, affected_areas, causal_chain,
                            suspected_drivers, status
                        ) VALUES (
                            :trace_id, :library_id, :signal_name,
                            :domain, :category, :trigger,
                            :metric, :metric_value, :baseline,
                            :variance, :severity, :severity_score,
                            :impact, CAST(:affected_areas AS jsonb), :causal_chain,
                            CAST(:drivers AS jsonb), 'NEW'
                        )
                    """),
                    {
                        'trace_id': trace_id,
                        'library_id': signal_data.get('signal_library_id', 'CUSTOM'),
                        'signal_name': signal_data.get('signal_name', 'Unknown Signal'),
                        'domain': signal_data.get('signal_domain', 'GENERAL'),
                        'category': signal_data.get('signal_category', 'TACTICAL'),
                        'trigger': signal_data.get('trigger_condition', ''),
                        'metric': signal_data.get('primary_metric', ''),
                        'metric_value': _num(signal_data.get('metric_value', 0)),
                        'baseline': _num(signal_data.get('baseline_value', 0)),
                        'variance': _num(signal_data.get('variance_pct', 0)),
                        'severity': signal_data.get('severity', 'MEDIUM'),
                        'severity_score': _num(signal_data.get('severity_score', 0.5), 0.5),
                        'impact': _num(signal_data.get('impact_amount', 0)),
                        'affected_areas': json.dumps(signal_data.get('affected_areas', {})),
                        'causal_chain': signal_data.get('causal_chain', ''),
                        'drivers': json.dumps(signal_data.get('suspected_drivers', []))
                    }
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to log signal: {e}")
    
    async def log_drift(
        self,
        trace_id: str,
        drift_data: Dict[str, Any]
    ):
        """Log detected drift
        
        Args:
            trace_id: Trace identifier
            drift_data: Drift detection results
        """
        try:
            with self._get_engine().connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO drift_detection_logs (
                            trace_id, drift_type, drift_subtype,
                            detection_method, confidence_score,
                            statistical_test, p_value, effect_size,
                            drift_description, affected_columns,
                            recommended_action
                        ) VALUES (
                            :trace_id, :drift_type, :drift_subtype,
                            :method, :confidence,
                            :stat_test, :p_value, :effect_size,
                            :description, :columns,
                            :action
                        )
                    """),
                    {
                        'trace_id': trace_id,
                        'drift_type': drift_data.get('drift_type', 'DATA_DRIFT'),
                        'drift_subtype': drift_data.get('drift_subtype', ''),
                        'method': drift_data.get('detection_method', 'statistical'),
                        'confidence': _num(drift_data.get('confidence', 0.5), 0.5),
                        'stat_test': drift_data.get('statistical_test', ''),
                        'p_value': _num(drift_data.get('p_value', 1.0), 1.0),
                        'effect_size': _num(drift_data.get('effect_size', 0)),
                        'description': drift_data.get('drift_description', ''),
                        'columns': drift_data.get('affected_columns', []),
                        'action': drift_data.get('recommended_action', '')
                    }
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to log drift: {e}")
    
    async def log_pipeline_complete(
        self,
        trace_id: str,
        context: AgentContext
    ):
        """Log pipeline completion
        
        Args:
            trace_id: Trace identifier
            context: Final agent context
        """
        try:
            with self._get_engine().connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO audit_trail (
                            trace_id, event_type, event_severity,
                            agent_name, event_description, event_data
                        ) VALUES (
                            :trace_id, 'pipeline_complete', 'info',
                            'orchestrator', 'Pipeline completed successfully',
                            CAST(:event_data AS jsonb)
                        )
                    """),
                    {
                        'trace_id': trace_id,
                        'event_data': json.dumps({
                            'final_context_keys': list(context.__dict__.keys()),
                            'timestamp': datetime.now().isoformat(),
                            'signals_detected': len(context.signal_context),
                            'drifts_detected': len(context.drift_context)
                        })
                    }
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to log pipeline completion: {e}")
    
    async def get_logs(
        self, 
        trace_id: str
    ) -> List[Dict[str, Any]]:
        """Retrieve all logs for a trace
        
        Args:
            trace_id: Trace identifier
            
        Returns:
            List of log entries
        """
        try:
            with self._get_engine().connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT * FROM agent_execution_logs 
                        WHERE trace_id = :trace_id
                        ORDER BY agent_step, timestamp
                    """),
                    {'trace_id': trace_id}
                )
                
                return [dict(row._mapping) for row in result]
        except Exception as e:
            logger.error(f"Failed to retrieve logs: {e}")
            return []
    
    async def get_sql_mappings(
        self, 
        trace_id: str
    ) -> List[Dict[str, Any]]:
        """Retrieve graph-to-SQL mappings for a trace
        
        Args:
            trace_id: Trace identifier
            
        Returns:
            List of graph-SQL mappings
        """
        try:
            with self._get_engine().connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT * FROM graph_sql_mappings 
                        WHERE trace_id = :trace_id
                        ORDER BY graph_id
                    """),
                    {'trace_id': trace_id}
                )
                
                return [dict(row._mapping) for row in result]
        except Exception as e:
            logger.error(f"Failed to retrieve SQL mappings: {e}")
            return []
    
    async def get_signals(
        self, 
        trace_id: str
    ) -> List[Dict[str, Any]]:
        """Retrieve detected signals for a trace
        
        Args:
            trace_id: Trace identifier
            
        Returns:
            List of detected signals
        """
        try:
            with self._get_engine().connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT * FROM signal_detection_logs 
                        WHERE trace_id = :trace_id
                        ORDER BY detection_timestamp
                    """),
                    {'trace_id': trace_id}
                )
                
                return [dict(row._mapping) for row in result]
        except Exception as e:
            logger.error(f"Failed to retrieve signals: {e}")
            return []
