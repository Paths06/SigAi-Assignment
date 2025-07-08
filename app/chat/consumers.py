"""
WebSocket consumer implementation for real-time chat functionality.
Handles message processing, connection management, and session persistence.
"""

import asyncio
import json
import logging
import signal
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.core.cache import cache
from django.conf import settings

from .metrics import (
    websocket_connections,
    websocket_messages,
    websocket_errors,
    websocket_heartbeats,
    websocket_shutdown_duration,
    track_websocket_duration
)

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer implementation with production features:
    - Message counting per connection
    - Server-side heartbeat every 30 seconds  
    - Graceful shutdown on SIGTERM
    - Session reconnection support
    - Metrics collection and structured logging
    """
    
    # Class-level tracking for active connections
    active_connections: Set['ChatConsumer'] = set()
    shutdown_initiated = False
    heartbeat_task: Optional[asyncio.Task] = None
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id: Optional[str] = None
        self.message_count: int = 0
        self.connection_start: float = 0
        self.connection_id: str = str(uuid.uuid4())
        self.graceful_close: bool = False
        
    async def connect(self):
        """Handle WebSocket connection with session recovery."""
        try:
            # Check if we're in shutdown mode
            if self.shutdown_initiated:
                logger.warning(f"Rejecting new connection during shutdown")
                await self.close(code=1001)
                return
                
            # Track connection start time
            self.connection_start = time.time()
            
            # Extract session ID from query params for reconnection
            query_params = self.scope.get('query_string', b'').decode()
            if 'session_uuid=' in query_params:
                self.session_id = query_params.split('session_uuid=')[1].split('&')[0]
                
                # Try to recover session from cache
                cached_count = await self.get_cached_session(self.session_id)
                if cached_count is not None:
                    self.message_count = cached_count
                    logger.info(f"Recovered session {self.session_id} with count {self.message_count}")
            else:
                self.session_id = str(uuid.uuid4())
                
            # Accept connection
            await self.accept()
            
            # Add to active connections
            self.active_connections.add(self)
            websocket_connections.inc()
            
            # Join room for broadcasts
            await self.channel_layer.group_add("broadcast", self.channel_name)
            
            # Start heartbeat if this is the first connection
            if len(self.active_connections) == 1 and not self.heartbeat_task:
                self.heartbeat_task = asyncio.create_task(self.heartbeat_loop())
                
            logger.info(
                f"WebSocket connected",
                extra={
                    "connection_id": self.connection_id,
                    "session_id": self.session_id,
                    "message_count": self.message_count,
                    "active_connections": len(self.active_connections)
                }
            )
            
        except Exception as e:
            websocket_errors.labels(error_type="connection_error").inc()
            logger.error(f"Connection error: {e}", exc_info=True)
            await self.close(code=1011)
            
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection with cleanup."""
        try:
            # Remove from active connections
            self.active_connections.discard(self)
            websocket_connections.dec()
            
            # Track connection duration
            if self.connection_start:
                duration = time.time() - self.connection_start
                track_websocket_duration(duration)
                
            # Leave broadcast group
            await self.channel_layer.group_discard("broadcast", self.channel_name)
            
            # Cache session for potential reconnection (5 minute TTL)
            if self.session_id and self.message_count > 0:
                await self.cache_session(self.session_id, self.message_count)
                
            # Send goodbye message if not already sent
            if not self.graceful_close and close_code != 1001:
                try:
                    await self.send(text_data=json.dumps({
                        "bye": True,
                        "total": self.message_count
                    }))
                except:
                    pass  # Connection might already be closed
                    
            # Stop heartbeat if this was the last connection
            if len(self.active_connections) == 0 and self.heartbeat_task:
                self.heartbeat_task.cancel()
                self.heartbeat_task = None
                
            logger.info(
                f"WebSocket disconnected",
                extra={
                    "connection_id": self.connection_id,
                    "session_id": self.session_id,
                    "close_code": close_code,
                    "message_count": self.message_count,
                    "duration": duration if self.connection_start else 0,
                    "active_connections": len(self.active_connections)
                }
            )
            
        except Exception as e:
            websocket_errors.labels(error_type="disconnect_error").inc()
            logger.error(f"Disconnect error: {e}", exc_info=True)
            
    async def receive(self, text_data):
        """Handle incoming WebSocket messages."""
        try:
            # Parse message
            data = json.loads(text_data)
            message = data.get('message', '')
            
            # Increment counter
            self.message_count += 1
            websocket_messages.inc()
            
            # Send response with count
            await self.send(text_data=json.dumps({
                "count": self.message_count
            }))
            
            logger.debug(
                f"Message received",
                extra={
                    "connection_id": self.connection_id,
                    "session_id": self.session_id,
                    "message_count": self.message_count,
                    "message_length": len(message)
                }
            )
            
        except json.JSONDecodeError:
            websocket_errors.labels(error_type="invalid_json").inc()
            await self.send(text_data=json.dumps({
                "error": "Invalid JSON"
            }))
        except Exception as e:
            websocket_errors.labels(error_type="receive_error").inc()
            logger.error(f"Receive error: {e}", exc_info=True)
            await self.send(text_data=json.dumps({
                "error": "Internal error"
            }))
            
    async def broadcast_message(self, event):
        """Handle broadcast messages from channel layer."""
        message = event['message']
        await self.send(text_data=json.dumps(message))
        
    @classmethod
    async def heartbeat_loop(cls):
        """Send heartbeat to all connections every 30 seconds."""
        while cls.active_connections and not cls.shutdown_initiated:
            try:
                await asyncio.sleep(30)
                
                if cls.shutdown_initiated:
                    break
                    
                timestamp = datetime.now(timezone.utc).isoformat()
                websocket_heartbeats.inc()
                
                # Broadcast heartbeat to all connections
                from channels.layers import get_channel_layer
                channel_layer = get_channel_layer()
                
                await channel_layer.group_send(
                    "broadcast",
                    {
                        "type": "broadcast_message",
                        "message": {"ts": timestamp}
                    }
                )
                
                logger.debug(f"Heartbeat sent to {len(cls.active_connections)} connections")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}", exc_info=True)
                
    @classmethod
    async def graceful_shutdown(cls):
        """Handle graceful shutdown on SIGTERM."""
        shutdown_start = time.time()
        cls.shutdown_initiated = True
        
        logger.info(f"Starting graceful shutdown of {len(cls.active_connections)} connections")
        
        # Cancel heartbeat
        if cls.heartbeat_task:
            cls.heartbeat_task.cancel()
            
        # Close all active connections with code 1001
        tasks = []
        for consumer in list(cls.active_connections):
            consumer.graceful_close = True
            
            # Send goodbye message
            try:
                await consumer.send(text_data=json.dumps({
                    "bye": True,
                    "total": consumer.message_count
                }))
            except:
                pass
                
            # Close connection
            tasks.append(consumer.close(code=1001))
            
        # Wait for all connections to close (max 10 seconds)
        if tasks:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=9.0  # Leave 1 second buffer
            )
            
        shutdown_duration = time.time() - shutdown_start
        websocket_shutdown_duration.observe(shutdown_duration)
        
        logger.info(f"Graceful shutdown completed in {shutdown_duration:.2f} seconds")
        
    @database_sync_to_async
    def get_cached_session(self, session_id: str) -> Optional[int]:
        """Retrieve session from cache."""
        key = f"ws_session:{session_id}"
        return cache.get(key)
        
    @database_sync_to_async
    def cache_session(self, session_id: str, count: int):
        """Cache session for reconnection."""
        key = f"ws_session:{session_id}"
        cache.set(key, count, timeout=300)  # 5 minute TTL


# Signal handler for graceful shutdown
def handle_sigterm(signum, frame):
    """Handle SIGTERM signal for graceful shutdown."""
    logger.info("Received SIGTERM, initiating graceful shutdown")
    asyncio.create_task(ChatConsumer.graceful_shutdown())
    
# Register signal handler
signal.signal(signal.SIGTERM, handle_sigterm)
