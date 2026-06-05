"""Base Agent class with standardized communication interface.

All agents in the system inherit from BaseAgent to ensure:
- Consistent messaging protocol
- Automatic logging integration
- Context sharing between agents
- Retry and fallback handling
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from enum import Enum
from datetime import datetime
import uuid
import logging

logger = logging.getLogger(__name__)


class MessageType(Enum):
    """Types of messages agents can exchange"""
    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"
    ERROR = "error"
    BROADCAST = "broadcast"


class AgentStatus(Enum):
    """Agent execution status"""
    IDLE = "idle"
    PROCESSING = "processing"
    WAITING = "waiting"
    ERROR = "error"
    COMPLETED = "completed"


@dataclass
class AgentMessage:
    """Standardized message format for agent communication"""
    msg_id: str
    correlation_id: str
    trace_id: str
    agent_from: str
    agent_to: Optional[str]  # None for broadcast
    msg_type: MessageType
    timestamp: datetime
    payload: Dict[str, Any]
    context: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    requires_response: bool = False
    timeout_seconds: int = 60


@dataclass
class AgentContext:
    """Shared context object passed between agents during pipeline execution"""
    trace_id: str
    query_context: Dict[str, Any] = field(default_factory=dict)
    schema_context: Dict[str, Any] = field(default_factory=dict)
    execution_context: Dict[str, Any] = field(default_factory=dict)
    signal_context: Dict[str, Any] = field(default_factory=dict)
    drift_context: Dict[str, Any] = field(default_factory=dict)
    graph_context: Dict[str, Any] = field(default_factory=dict)
    insight_context: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    ttl_seconds: int = 3600

    def is_expired(self) -> bool:
        """Check if context has expired"""
        from datetime import timedelta
        return datetime.now() > self.timestamp + timedelta(seconds=self.ttl_seconds)


class BaseAgent(ABC):
    """Base class for all agents with standardized communication
    
    Usage:
        class MyAgent(BaseAgent):
            async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
                # Agent logic here
                return {'result': 'success'}
    """
    
    def __init__(
        self, 
        agent_name: str, 
        event_bus: Optional['AgentEventBus'] = None,
        logging_agent: Optional['LoggingAgent'] = None
    ):
        self.agent_name = agent_name
        self.event_bus = event_bus
        self.logging_agent = logging_agent
        self.status = AgentStatus.IDLE
        self.message_handlers: Dict[str, Callable] = {}
        self._context: Optional[AgentContext] = None
        
        # Register with event bus if provided
        if event_bus:
            event_bus.register_agent(self)
        
        logger.info(f"Agent '{agent_name}' initialized")
    
    @property
    def context(self) -> Optional[AgentContext]:
        """Get current agent context"""
        return self._context
    
    @context.setter
    def context(self, ctx: Optional[AgentContext]):
        """Set agent context"""
        self._context = ctx
    
    async def send_message(
        self,
        to_agent: Optional[str],
        payload: Dict[str, Any],
        msg_type: MessageType = MessageType.REQUEST,
        requires_response: bool = False,
        timeout: int = 60
    ) -> Optional[AgentMessage]:
        """Send message to another agent or broadcast
        
        Args:
            to_agent: Target agent name (None for broadcast)
            payload: Message payload
            msg_type: Type of message
            requires_response: Whether to wait for response
            timeout: Timeout in seconds
            
        Returns:
            Response message if requires_response=True, else None
        """
        if not self.event_bus:
            logger.warning(f"Agent '{self.agent_name}' has no event bus")
            return None
        
        message = AgentMessage(
            msg_id=str(uuid.uuid4()),
            correlation_id=str(uuid.uuid4()),
            trace_id=self._context.trace_id if self._context else str(uuid.uuid4()),
            agent_from=self.agent_name,
            agent_to=to_agent,
            msg_type=msg_type,
            timestamp=datetime.now(),
            payload=payload,
            context=self._serialize_context(),
            confidence=1.0,
            requires_response=requires_response,
            timeout_seconds=timeout
        )
        
        return await self.event_bus.send(message)
    
    async def receive_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Receive and process incoming message
        
        Args:
            message: Incoming message
            
        Returns:
            Response message if applicable
        """
        logger.debug(f"Agent '{self.agent_name}' received message from '{message.agent_from}'")
        
        # Update context from incoming message
        if message.context:
            self._deserialize_context(message.context)
        
        # Route to appropriate handler
        action = message.payload.get('action')
        handler = self.message_handlers.get(action)
        
        if handler:
            try:
                return await handler(message)
            except Exception as e:
                logger.error(f"Handler for action '{action}' failed: {e}")
                return self._create_error_response(message, str(e))
        
        return None
    
    def register_handler(self, action: str, handler: Callable):
        """Register a message handler for a specific action
        
        Args:
            action: Action name to handle
            handler: Async function to handle the action
        """
        self.message_handlers[action] = handler
        logger.debug(f"Agent '{self.agent_name}' registered handler for '{action}'")
    
    @abstractmethod
    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Main execution method - must be implemented by subclasses
        
        This is the core agent logic. All agents must implement this method.
        
        Args:
            input_data: Input data for the agent
            
        Returns:
            Agent execution results
        """
        pass
    
    async def execute_with_logging(
        self, 
        input_data: Dict[str, Any],
        step_number: int = 0
    ) -> Dict[str, Any]:
        """Execute agent with automatic logging
        
        Args:
            input_data: Input data for the agent
            step_number: Step number in pipeline
            
        Returns:
            Agent execution results
        """
        import time
        
        start_time = time.time()
        self.status = AgentStatus.PROCESSING
        
        # Log start
        if self.logging_agent and self._context:
            await self.logging_agent.log_agent_start(
                self._context.trace_id,
                self.agent_name,
                step_number,
                input_data
            )
        
        try:
            # Execute agent logic
            result = await self.execute(input_data)
            
            duration = time.time() - start_time
            self.status = AgentStatus.COMPLETED
            
            # Log completion
            if self.logging_agent and self._context:
                await self.logging_agent.log_agent_complete(
                    self._context.trace_id,
                    self.agent_name,
                    result,
                    duration,
                    0  # attempt number
                )
            
            return {
                'status': 'success',
                'result': result,
                'duration_seconds': duration,
                'agent_name': self.agent_name
            }
            
        except Exception as e:
            duration = time.time() - start_time
            self.status = AgentStatus.ERROR
            
            # Log failure
            if self.logging_agent and self._context:
                await self.logging_agent.log_agent_failure(
                    self._context.trace_id,
                    self.agent_name,
                    str(e),
                    0,
                    duration
                )
            
            return {
                'status': 'failed',
                'error': str(e),
                'duration_seconds': duration,
                'agent_name': self.agent_name
            }
    
    def _serialize_context(self) -> Dict[str, Any]:
        """Convert context to serializable dict"""
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
    
    def _deserialize_context(self, data: Dict[str, Any]):
        """Reconstruct context from dict"""
        if not data:
            return
        
        try:
            self._context = AgentContext(
                trace_id=data['trace_id'],
                query_context=data.get('query_context', {}),
                schema_context=data.get('schema_context', {}),
                execution_context=data.get('execution_context', {}),
                signal_context=data.get('signal_context', {}),
                drift_context=data.get('drift_context', {}),
                graph_context=data.get('graph_context', {}),
                insight_context=data.get('insight_context', {}),
                timestamp=datetime.fromisoformat(data['timestamp']),
                ttl_seconds=data.get('ttl_seconds', 3600)
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to deserialize context: {e}")
    
    def _create_error_response(
        self, 
        original_message: AgentMessage, 
        error: str
    ) -> AgentMessage:
        """Create error response message"""
        return AgentMessage(
            msg_id=str(uuid.uuid4()),
            correlation_id=original_message.correlation_id,
            trace_id=original_message.trace_id,
            agent_from=self.agent_name,
            agent_to=original_message.agent_from,
            msg_type=MessageType.ERROR,
            timestamp=datetime.now(),
            payload={'error': error, 'original_action': original_message.payload.get('action')},
            context=self._serialize_context(),
            confidence=0.0
        )
