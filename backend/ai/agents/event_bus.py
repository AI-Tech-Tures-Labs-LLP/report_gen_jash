"""Agent Event Bus for asynchronous agent communication.

Provides pub/sub messaging between agents with:
- Point-to-point messaging
- Broadcast messaging
- Request-response patterns
- Message routing and delivery
"""

import asyncio
from typing import Dict, List, Optional, TYPE_CHECKING
from datetime import datetime
import logging
import uuid

if TYPE_CHECKING:
    from .base_agent import BaseAgent, AgentMessage

logger = logging.getLogger(__name__)


class AgentEventBus:
    """Central event bus for agent communication
    
    Usage:
        bus = AgentEventBus()
        
        # Register agents
        bus.register_agent(my_agent)
        
        # Send message
        await bus.send(message)
        
        # Broadcast
        await bus.broadcast(payload, from_agent='agent1')
    """
    
    def __init__(self):
        self.agents: Dict[str, 'BaseAgent'] = {}
        self.message_history: List[Dict] = []
        self.max_history = 1000
        self._pending_responses: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
    
    def register_agent(self, agent: 'BaseAgent'):
        """Register an agent with the bus
        
        Args:
            agent: Agent instance to register
        """
        self.agents[agent.agent_name] = agent
        logger.info(f"Registered agent '{agent.agent_name}' with event bus")
    
    def unregister_agent(self, agent_name: str):
        """Unregister an agent from the bus
        
        Args:
            agent_name: Name of agent to unregister
        """
        if agent_name in self.agents:
            del self.agents[agent_name]
            logger.info(f"Unregistered agent '{agent_name}' from event bus")
    
    async def send(self, message: 'AgentMessage') -> Optional['AgentMessage']:
        """Send message and optionally wait for response
        
        Args:
            message: Message to send
            
        Returns:
            Response message if requires_response=True, else None
        """
        from .base_agent import MessageType
        
        # Log message
        await self._log_message(message)
        
        # Store pending response if needed
        future = None
        if message.requires_response:
            future = asyncio.Future()
            self._pending_responses[message.msg_id] = future
        
        # Route message
        if message.agent_to:
            # Point-to-point
            target = self.agents.get(message.agent_to)
            if target:
                asyncio.create_task(self._deliver_message(target, message))
            else:
                logger.warning(f"Target agent '{message.agent_to}' not found")
                if future and not future.done():
                    future.set_exception(ValueError(f"Agent '{message.agent_to}' not found"))
        else:
            # Broadcast
            await self._broadcast_message(message)
        
        # Wait for response if required
        if future:
            try:
                return await asyncio.wait_for(future, timeout=message.timeout_seconds)
            except asyncio.TimeoutError:
                logger.warning(f"Message {message.msg_id[:8]}... timed out waiting for response")
                return None
            finally:
                self._pending_responses.pop(message.msg_id, None)
        
        return None
    
    async def _deliver_message(self, agent: 'BaseAgent', message: 'AgentMessage'):
        """Deliver message to specific agent"""
        try:
            response = await agent.receive_message(message)
            
            # If there's a pending response future, set it
            if response and message.requires_response:
                future = self._pending_responses.get(message.msg_id)
                if future and not future.done():
                    future.set_result(response)
                    
        except Exception as e:
            logger.error(f"Error delivering message to '{agent.agent_name}': {e}")
            
            # Notify pending future of error
            if message.requires_response:
                future = self._pending_responses.get(message.msg_id)
                if future and not future.done():
                    future.set_exception(e)
    
    async def _broadcast_message(self, message: 'AgentMessage'):
        """Broadcast message to all agents except sender"""
        tasks = []
        
        for agent in self.agents.values():
            if agent.agent_name != message.agent_from:
                task = asyncio.create_task(self._deliver_message(agent, message))
                tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def broadcast(
        self, 
        payload: Dict, 
        from_agent: str,
        msg_type: 'MessageType' = None,
        trace_id: Optional[str] = None
    ):
        """Broadcast a message to all agents
        
        Args:
            payload: Message payload
            from_agent: Sending agent name
            msg_type: Type of message
            trace_id: Optional trace ID
        """
        from .base_agent import AgentMessage, MessageType
        
        if msg_type is None:
            msg_type = MessageType.BROADCAST
        
        message = AgentMessage(
            msg_id=str(uuid.uuid4()),
            correlation_id=str(uuid.uuid4()),
            trace_id=trace_id or str(uuid.uuid4()),
            agent_from=from_agent,
            agent_to=None,  # Broadcast
            msg_type=msg_type,
            timestamp=datetime.now(),
            payload=payload,
            context={},
            confidence=1.0,
            requires_response=False
        )
        
        await self.send(message)
    
    async def request(
        self,
        to_agent: str,
        payload: Dict,
        from_agent: str,
        timeout: int = 60,
        trace_id: Optional[str] = None
    ) -> Optional['AgentMessage']:
        """Send a request and wait for response
        
        Args:
            to_agent: Target agent name
            payload: Request payload
            from_agent: Sending agent name
            timeout: Timeout in seconds
            trace_id: Optional trace ID
            
        Returns:
            Response message or None if timed out
        """
        from .base_agent import AgentMessage, MessageType
        
        message = AgentMessage(
            msg_id=str(uuid.uuid4()),
            correlation_id=str(uuid.uuid4()),
            trace_id=trace_id or str(uuid.uuid4()),
            agent_from=from_agent,
            agent_to=to_agent,
            msg_type=MessageType.REQUEST,
            timestamp=datetime.now(),
            payload=payload,
            context={},
            confidence=1.0,
            requires_response=True,
            timeout_seconds=timeout
        )
        
        return await self.send(message)
    
    async def _log_message(self, message: 'AgentMessage'):
        """Log message to history"""
        async with self._lock:
            self.message_history.append({
                'msg_id': message.msg_id,
                'trace_id': message.trace_id,
                'from': message.agent_from,
                'to': message.agent_to,
                'type': message.msg_type.value,
                'timestamp': message.timestamp.isoformat(),
                'payload_keys': list(message.payload.keys())
            })
            
            # Trim history if too large
            if len(self.message_history) > self.max_history:
                self.message_history = self.message_history[-self.max_history:]
    
    def get_message_history(
        self, 
        trace_id: Optional[str] = None,
        agent_name: Optional[str] = None
    ) -> List[Dict]:
        """Get message history, optionally filtered
        
        Args:
            trace_id: Filter by trace ID
            agent_name: Filter by agent name
            
        Returns:
            List of message history entries
        """
        filtered = self.message_history
        
        if trace_id:
            filtered = [m for m in filtered if m['trace_id'] == trace_id]
        
        if agent_name:
            filtered = [
                m for m in filtered 
                if m['from'] == agent_name or m['to'] == agent_name
            ]
        
        return filtered
    
    def get_registered_agents(self) -> List[str]:
        """Get list of registered agent names"""
        return list(self.agents.keys())
