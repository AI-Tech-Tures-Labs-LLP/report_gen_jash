"""Agent Orchestrator for pipeline execution with retry and parallelization.

Manages the flow of agents through the pipeline:
- Parallel execution where possible
- Retry logic with exponential backoff
- Fallback agent support
- Dependency-based execution ordering
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
import logging

from .base_agent import BaseAgent, AgentContext, AgentMessage, MessageType
from .logging_agent import LoggingAgent

logger = logging.getLogger(__name__)


@dataclass
class AgentStep:
    """Configuration for a single agent step in the pipeline"""
    agent_name: str
    step_number: int
    max_retries: int = 2
    retry_delay_seconds: int = 1
    parallel_with: Optional[List[str]] = field(default=None)  # Run in parallel with these agents
    skip_on_failure: bool = False
    fallback_agent: Optional[str] = None
    timeout_seconds: int = 60
    
    def __post_init__(self):
        if self.parallel_with is None:
            self.parallel_with = []


@dataclass
class PipelineConfig:
    """Configuration for the entire pipeline"""
    enable_parallel: bool = True
    enable_caching: bool = True
    enable_logging: bool = True
    max_concurrent_agents: int = 5
    default_agent_timeout: int = 60


class AgentPipelineError(Exception):
    """Custom exception for pipeline failures"""
    pass


class AgentOrchestrator:
    """Orchestrates multi-agent pipeline with retry logic and parallel execution
    
    Usage:
        orchestrator = AgentOrchestrator(event_bus, logging_agent)
        
        # Configure pipeline steps
        steps = [
            AgentStep('context_agent', 1),
            AgentStep('business_analyst', 2),
            AgentStep('sql_agent', 3),
            AgentStep('data_analyst', 4),
            AgentStep('report_writer', 5),
            AgentStep('qa_agent', 6),
        ]
        
        orchestrator.configure_pipeline(steps)
        
        # Execute
        result = await orchestrator.execute_pipeline(
            initial_input={'question': 'Revenue trend'},
            trace_id=str(uuid.uuid4())
        )
    """
    
    def __init__(
        self, 
        event_bus: 'AgentEventBus',
        logging_agent: Optional[LoggingAgent] = None,
        config: Optional[PipelineConfig] = None
    ):
        self.event_bus = event_bus
        self.logging_agent = logging_agent
        self.config = config or PipelineConfig()
        self.steps: List[AgentStep] = []
        self.results: Dict[str, Any] = {}
        self._context: Optional[AgentContext] = None
        self._trace_id: Optional[str] = None
    
    def configure_pipeline(self, steps: List[AgentStep]):
        """Configure the agent execution pipeline
        
        Args:
            steps: List of agent steps in execution order
        """
        self.steps = sorted(steps, key=lambda s: s.step_number)
        logger.info(f"Configured pipeline with {len(steps)} steps")
    
    async def execute_pipeline(
        self, 
        initial_input: Dict[str, Any],
        trace_id: str,
        context: Optional[AgentContext] = None
    ) -> Dict[str, Any]:
        """Execute the full agent pipeline
        
        Args:
            initial_input: Initial input data (e.g., user query)
            trace_id: Unique trace identifier for this execution
            context: Optional pre-existing context
            
        Returns:
            Pipeline execution results
        """
        from datetime import datetime
        
        self._trace_id = trace_id
        pipeline_start = datetime.now()
        
        # Initialize context
        if context:
            self._context = context
            self._context.trace_id = trace_id
        else:
            self._context = AgentContext(
                trace_id=trace_id,
                query_context=initial_input,
                timestamp=datetime.now()
            )
        
        self.results = {}
        
        logger.info(f"Pipeline {trace_id[:8]}... starting with {len(self.steps)} steps")
        
        # Log pipeline start
        if self.logging_agent and self.config.enable_logging:
            await self.logging_agent.log_pipeline_start(
                trace_id,
                initial_input.get('question', ''),
                initial_input.get('intent_mode', 'STANDARD_REPORT')
            )
        
        try:
            # Group steps for parallel execution
            if self.config.enable_parallel:
                step_groups = self._group_parallel_steps(self.steps)
            else:
                step_groups = [[step] for step in self.steps]
            
            # Execute each group
            for group_idx, group in enumerate(step_groups):
                logger.info(f"Executing step group {group_idx + 1}/{len(step_groups)} with {len(group)} agent(s)")
                
                group_results = await self._execute_parallel_group(group)
                
                # Process results and update context
                for agent_name, result in group_results.items():
                    self._update_context(agent_name, result)
                    
                    # Check for critical failures
                    if result.get('status') == 'failed':
                        step_config = next((s for s in group if s.agent_name == agent_name), None)
                        
                        if step_config and not step_config.skip_on_failure:
                            logger.error(f"Pipeline failed at agent '{agent_name}': {result.get('error')}")
                            
                            if self.logging_agent:
                                await self.logging_agent.log_pipeline_failure(
                                    trace_id, agent_name, result.get('error', 'Unknown error')
                                )
                            
                            raise AgentPipelineError(
                                f"Agent {agent_name} failed: {result.get('error')}"
                            )
                        else:
                            logger.warning(f"Agent '{agent_name}' failed but skip_on_failure=True, continuing...")
            
            # Calculate total duration
            pipeline_end = datetime.now()
            total_duration = (pipeline_end - pipeline_start).total_seconds()
            
            logger.info(f"Pipeline {trace_id[:8]}... completed in {total_duration:.2f}s")
            
            # Log pipeline completion
            if self.logging_agent and self.config.enable_logging:
                await self.logging_agent.log_pipeline_complete(trace_id, self._context)
            
            return {
                'trace_id': trace_id,
                'status': 'success',
                'duration_seconds': total_duration,
                'context': self._serialize_context(),
                'results': self.results,
                'step_count': len(self.steps),
                'completed_steps': len(self.results)
            }
            
        except Exception as e:
            logger.error(f"Pipeline {trace_id[:8]}... failed: {e}")
            
            if self.logging_agent:
                await self.logging_agent.log_agent_failure(
                    trace_id, 'orchestrator', str(e), 0
                )
            
            return {
                'trace_id': trace_id,
                'status': 'failed',
                'error': str(e),
                'context': self._serialize_context(),
                'results': self.results
            }
    
    def _group_parallel_steps(self, steps: List[AgentStep]) -> List[List[AgentStep]]:
        """Group steps that can run in parallel
        
        Args:
            steps: List of all steps
            
        Returns:
            List of step groups (each group can execute in parallel)
        """
        if not steps:
            return []
        
        groups = []
        current_group = []
        current_step = steps[0].step_number
        
        for step in steps:
            if step.step_number == current_step:
                current_group.append(step)
            else:
                groups.append(current_group)
                current_group = [step]
                current_step = step.step_number
        
        if current_group:
            groups.append(current_group)
        
        return groups
    
    async def _execute_parallel_group(
        self, 
        group: List[AgentStep]
    ) -> Dict[str, Dict[str, Any]]:
        """Execute a group of agents in parallel
        
        Args:
            group: List of steps to execute
            
        Returns:
            Dictionary of agent_name -> result
        """
        # Create tasks for all agents in group
        tasks = []
        for step in group:
            task = self._execute_agent_with_retry(step)
            tasks.append((step.agent_name, task))
        
        # Execute all concurrently
        results = {}
        for agent_name, task in tasks:
            try:
                result = await task
                results[agent_name] = result
            except Exception as e:
                logger.error(f"Exception executing agent '{agent_name}': {e}")
                results[agent_name] = {
                    'status': 'failed',
                    'error': str(e),
                    'agent_name': agent_name
                }
        
        return results
    
    async def _execute_agent_with_retry(
        self, 
        step: AgentStep
    ) -> Dict[str, Any]:
        """Execute an agent with retry logic
        
        Args:
            step: Agent step configuration
            
        Returns:
            Agent execution result
        """
        agent = self.event_bus.agents.get(step.agent_name)
        
        if not agent:
            return {
                'status': 'failed',
                'error': f"Agent '{step.agent_name}' not found in event bus",
                'agent_name': step.agent_name
            }
        
        last_error = None
        
        for attempt in range(step.max_retries + 1):
            try:
                # Set agent context
                agent.context = self._context
                
                # Log start
                if self.logging_agent and self.config.enable_logging:
                    await self.logging_agent.log_agent_start(
                        self._trace_id,
                        step.agent_name,
                        step.step_number,
                        {'attempt': attempt, 'step': step.step_number}
                    )
                
                # Execute with timeout
                start_time = asyncio.get_event_loop().time()
                
                result = await asyncio.wait_for(
                    agent.execute({
                        'step_number': step.step_number,
                        'attempt': attempt,
                        'previous_results': self.results,
                        'trace_id': self._trace_id
                    }),
                    timeout=step.timeout_seconds
                )
                
                duration = asyncio.get_event_loop().time() - start_time
                
                # Log completion
                if self.logging_agent and self.config.enable_logging:
                    await self.logging_agent.log_agent_complete(
                        self._trace_id,
                        step.agent_name,
                        result,
                        duration,
                        attempt
                    )
                
                return {
                    'status': 'success',
                    'result': result,
                    'duration_seconds': duration,
                    'attempt': attempt,
                    'agent_name': step.agent_name
                }
                
            except asyncio.TimeoutError:
                last_error = f"Timeout after {step.timeout_seconds}s"
                logger.warning(f"Agent '{step.agent_name}' attempt {attempt + 1} timed out")
                
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Agent '{step.agent_name}' attempt {attempt + 1} failed: {e}")
            
            # Retry with exponential backoff
            if attempt < step.max_retries:
                delay = step.retry_delay_seconds * (2 ** attempt)
                logger.info(f"Retrying agent '{step.agent_name}' in {delay}s...")
                await asyncio.sleep(delay)
            
            # Try fallback agent if configured
            if attempt == step.max_retries and step.fallback_agent:
                fallback = self.event_bus.agents.get(step.fallback_agent)
                if fallback:
                    logger.info(f"Trying fallback agent '{step.fallback_agent}'")
                    try:
                        fallback.context = self._context
                        result = await asyncio.wait_for(
                            fallback.execute({
                                'step_number': step.step_number,
                                'attempt': 0,
                                'is_fallback': True
                            }),
                            timeout=step.timeout_seconds
                        )
                        
                        return {
                            'status': 'success',
                            'result': result,
                            'fallback_used': True,
                            'original_error': last_error,
                            'agent_name': step.fallback_agent
                        }
                    except Exception as e:
                        last_error = f"Fallback also failed: {e}"
        
        # All retries exhausted
        logger.error(f"Agent '{step.agent_name}' failed after {step.max_retries} retries")
        
        if self.logging_agent and self.config.enable_logging:
            await self.logging_agent.log_agent_failure(
                self._trace_id,
                step.agent_name,
                last_error,
                step.max_retries
            )
        
        return {
            'status': 'failed',
            'error': last_error,
            'retries_exhausted': True,
            'agent_name': step.agent_name
        }
    
    def _update_context(self, agent_name: str, result: Dict[str, Any]):
        """Update shared context based on agent results
        
        Args:
            agent_name: Name of the agent
            result: Agent execution result
        """
        if result.get('status') != 'success':
            return
        
        agent_result = result.get('result', {})
        
        # Route results to appropriate context section
        if agent_name in ['context_agent', 'query_understanding']:
            self._context.query_context.update(agent_result)
        elif agent_name in ['sql_generation', 'sql_agent']:
            self._context.execution_context['sql_plan'] = agent_result
        elif agent_name in ['sql_execution']:
            self._context.execution_context['results'] = agent_result
        elif agent_name in ['graph_selection']:
            self._context.graph_context.update(agent_result)
        elif agent_name in ['drift_detection']:
            self._context.drift_context.update(agent_result)
        elif agent_name in ['signal_intelligence', 'signal_detection']:
            self._context.signal_context.update(agent_result)
        elif agent_name in ['insight_generation', 'report_writer']:
            self._context.insight_context.update(agent_result)
        
        # Store in results dict
        self.results[agent_name] = agent_result
    
    def _serialize_context(self) -> Dict[str, Any]:
        """Serialize context to dictionary"""
        if not self._context:
            return {}
        
        return {
            'trace_id': self._context.trace_id,
            'query_context': self._context.query_context,
            'schema_context': self._context.schema_context,
            'execution_context': self._context.execution_context,
            'signal_context': self._context.signal_context,
            'drift_context': self._context.drift_context,
            'graph_context': self._context.graph_context,
            'insight_context': self._context.insight_context,
            'timestamp': self._context.timestamp.isoformat(),
            'ttl_seconds': self._context.ttl_seconds
        }
    
    def get_pipeline_status(self) -> Dict[str, Any]:
        """Get current pipeline execution status
        
        Returns:
            Pipeline status information
        """
        return {
            'trace_id': self._trace_id,
            'configured_steps': [s.agent_name for s in self.steps],
            'completed_steps': list(self.results.keys()),
            'pending_steps': [
                s.agent_name for s in self.steps 
                if s.agent_name not in self.results
            ],
            'context_keys': list(self._serialize_context().keys()) if self._context else []
        }
