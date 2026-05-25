-- Migration 001: Add agent logging and intelligence tables
-- Phase 1 of Enterprise Architecture Implementation

BEGIN;

-- Core agent execution logs
CREATE TABLE IF NOT EXISTS agent_execution_logs (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID NOT NULL,
    correlation_id UUID NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    
    -- Request context
    user_query TEXT NOT NULL,
    intent_detected VARCHAR(50) NOT NULL,
    intent_confidence DECIMAL(3,2),
    
    -- Agent flow
    agent_name VARCHAR(100) NOT NULL,
    agent_step INTEGER NOT NULL,
    agent_start_time TIMESTAMPTZ,
    agent_end_time TIMESTAMPTZ,
    agent_duration_ms INTEGER,
    
    -- Execution details
    input_context JSONB,
    output_result JSONB,
    confidence_score DECIMAL(3,2),
    
    -- Error handling
    status VARCHAR(20) NOT NULL DEFAULT 'success',
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    
    -- Performance
    llm_calls_count INTEGER DEFAULT 0,
    tool_calls_count INTEGER DEFAULT 0,
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    cache_hits INTEGER DEFAULT 0,
    
    -- Metadata
    model_used VARCHAR(50),
    system_fingerprint VARCHAR(100)
);

-- Graph-to-SQL traceability
CREATE TABLE IF NOT EXISTS graph_sql_mappings (
    mapping_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID NOT NULL,
    graph_id VARCHAR(50) NOT NULL,
    graph_title VARCHAR(255),
    graph_type VARCHAR(50),
    
    -- SQL linkage
    sql_query_hash VARCHAR(64),
    sql_query TEXT,
    sql_validated BOOLEAN DEFAULT FALSE,
    sql_validation_issues JSONB,
    
    -- Execution
    sql_execution_time_ms INTEGER,
    rows_returned INTEGER,
    data_hash VARCHAR(64),
    
    -- Graph selection reasoning
    graph_selection_reason TEXT,
    graph_selection_confidence DECIMAL(3,2),
    alternative_graphs_considered JSONB,
    
    -- Signals detected in this graph
    signals_found JSONB,
    drifts_found JSONB,
    
    -- Insight linkage
    related_insight_ids TEXT[]
);

-- SQL query execution logs
CREATE TABLE IF NOT EXISTS sql_execution_logs (
    execution_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID NOT NULL,
    query_hash VARCHAR(64) NOT NULL,
    query_text TEXT NOT NULL,
    query_type VARCHAR(50),
    
    -- Timing
    execution_start TIMESTAMPTZ,
    execution_end TIMESTAMPTZ,
    execution_duration_ms INTEGER,
    
    -- Results
    rows_returned INTEGER,
    columns_returned TEXT[],
    data_sample JSONB,
    
    -- Validation
    validation_passed BOOLEAN,
    validation_errors JSONB,
    
    -- Caching
    cache_key VARCHAR(255),
    cache_hit BOOLEAN DEFAULT FALSE,
    
    -- Filters applied
    filters_applied JSONB,
    aggregations_applied TEXT[]
);

-- AI Insight generation logs
CREATE TABLE IF NOT EXISTS insight_generation_logs (
    insight_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID NOT NULL,
    graph_id VARCHAR(50),
    
    -- Insight content
    insight_title VARCHAR(255),
    insight_body TEXT,
    insight_type VARCHAR(20),
    
    -- Reasoning chain
    reasoning_steps JSONB,
    confidence_score DECIMAL(3,2),
    
    -- Data sources
    data_sources TEXT[],
    sql_queries_used TEXT[],
    
    -- Validation
    human_reviewed BOOLEAN DEFAULT FALSE,
    review_feedback TEXT,
    accuracy_rating INTEGER
);

-- Signal detection logs
CREATE TABLE IF NOT EXISTS signal_detection_logs (
    signal_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID NOT NULL,
    detection_timestamp TIMESTAMPTZ DEFAULT NOW(),
    
    -- Signal classification
    signal_library_id VARCHAR(20),
    signal_name VARCHAR(100),
    signal_domain VARCHAR(50),
    signal_category VARCHAR(50),
    
    -- Detection context
    trigger_condition TEXT,
    primary_metric VARCHAR(100),
    metric_value DECIMAL(18,4),
    baseline_value DECIMAL(18,4),
    variance_pct DECIMAL(8,4),
    
    -- Severity assessment
    severity VARCHAR(20),
    severity_score DECIMAL(4,3),
    
    -- Impact analysis
    impact_currency VARCHAR(3) DEFAULT 'INR',
    impact_amount DECIMAL(18,2),
    affected_areas JSONB,
    
    -- Causal chain
    causal_chain TEXT,
    suspected_drivers JSONB,
    
    -- Status tracking
    status VARCHAR(20) DEFAULT 'NEW',
    assigned_to VARCHAR(100),
    resolution_notes TEXT
);

-- Drift detection logs
CREATE TABLE IF NOT EXISTS drift_detection_logs (
    drift_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID NOT NULL,
    detection_timestamp TIMESTAMPTZ DEFAULT NOW(),
    
    -- Drift classification
    drift_type VARCHAR(50),
    drift_subtype VARCHAR(50),
    
    -- Detection metadata
    detection_method VARCHAR(50),
    confidence_score DECIMAL(3,2),
    
    -- Comparison data
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    baseline_period_start TIMESTAMPTZ,
    baseline_period_end TIMESTAMPTZ,
    
    -- Statistical measures
    statistical_test VARCHAR(50),
    p_value DECIMAL(8,6),
    effect_size DECIMAL(8,4),
    
    -- Drift details
    drift_description TEXT,
    affected_columns TEXT[],
    sample_before JSONB,
    sample_after JSONB,
    
    -- Actionability
    recommended_action TEXT,
    auto_mitigation_triggered BOOLEAN DEFAULT FALSE
);

-- Comprehensive audit trail
CREATE TABLE IF NOT EXISTS audit_trail (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID NOT NULL,
    event_timestamp TIMESTAMPTZ DEFAULT NOW(),
    
    event_type VARCHAR(50) NOT NULL,
    event_severity VARCHAR(20) DEFAULT 'info',
    
    -- Actor
    agent_name VARCHAR(100),
    user_id VARCHAR(100),
    session_id VARCHAR(100),
    
    -- Event details
    event_description TEXT,
    event_data JSONB,
    
    -- Relationships
    parent_event_id UUID REFERENCES audit_trail(audit_id),
    related_entities JSONB
);

-- Agent configuration
CREATE TABLE IF NOT EXISTS agent_configurations (
    agent_name VARCHAR(100) PRIMARY KEY,
    agent_type VARCHAR(50),
    model_config JSONB,
    timeout_seconds INTEGER DEFAULT 60,
    max_retries INTEGER DEFAULT 2,
    fallback_agent VARCHAR(100),
    is_enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Query cache metadata
CREATE TABLE IF NOT EXISTS query_cache_metadata (
    query_hash VARCHAR(64) PRIMARY KEY,
    query_text TEXT,
    query_type VARCHAR(50),
    table_names TEXT[],
    column_names TEXT[],
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ DEFAULT NOW(),
    use_count INTEGER DEFAULT 1,
    avg_execution_time_ms INTEGER,
    cache_hits INTEGER DEFAULT 0
);

-- Performance metrics
CREATE TABLE IF NOT EXISTS performance_metrics (
    metric_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    metric_name VARCHAR(100),
    metric_value DECIMAL(18,6),
    labels JSONB
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_agent_logs_trace ON agent_execution_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_time ON agent_execution_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_agent_logs_step ON agent_execution_logs(agent_name, agent_step);

CREATE INDEX IF NOT EXISTS idx_graph_mappings_trace ON graph_sql_mappings(trace_id);
CREATE INDEX IF NOT EXISTS idx_graph_mappings_graph ON graph_sql_mappings(trace_id, graph_id);

CREATE INDEX IF NOT EXISTS idx_sql_exec_trace ON sql_execution_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_sql_exec_hash ON sql_execution_logs(query_hash);
CREATE INDEX IF NOT EXISTS idx_sql_exec_type ON sql_execution_logs(query_type);

CREATE INDEX IF NOT EXISTS idx_insight_trace ON insight_generation_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_insight_type ON insight_generation_logs(insight_type);

CREATE INDEX IF NOT EXISTS idx_signal_trace ON signal_detection_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_signal_library ON signal_detection_logs(signal_library_id);
CREATE INDEX IF NOT EXISTS idx_signal_time ON signal_detection_logs(detection_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signal_status ON signal_detection_logs(status);

CREATE INDEX IF NOT EXISTS idx_drift_trace ON drift_detection_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_drift_type ON drift_detection_logs(drift_type);
CREATE INDEX IF NOT EXISTS idx_drift_time ON drift_detection_logs(detection_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_trail(trace_id);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_trail(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_trail(event_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_perf_name_time ON performance_metrics(metric_name, timestamp DESC);

-- Partial index for active signals
CREATE INDEX IF NOT EXISTS idx_active_signals 
ON signal_detection_logs(signal_library_id, detection_timestamp) 
WHERE status IN ('NEW', 'INVESTIGATING');

-- Insert default agent configurations
INSERT INTO agent_configurations (agent_name, agent_type, timeout_seconds, max_retries) VALUES
('context_agent', 'intent_classification', 30, 1),
('business_analyst', 'blueprint_design', 60, 2),
('sql_agent', 'query_execution', 120, 2),
('data_analyst', 'validation', 30, 1),
('report_writer', 'narrative', 60, 1),
('qa_agent', 'quality_check', 30, 1),
('graph_selection', 'visualization', 20, 1),
('drift_detection', 'intelligence', 45, 1),
('insight_generation', 'narrative', 45, 1)
ON CONFLICT (agent_name) DO NOTHING;

-- Migration tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(10) PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT NOW(),
    description TEXT
);

INSERT INTO schema_migrations (version, description)
VALUES ('001', 'Add agent logging and intelligence tables');

COMMIT;
