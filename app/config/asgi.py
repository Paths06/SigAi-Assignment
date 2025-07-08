"""
ASGI config for Django WebSocket application.
Configures Channels with proper routing and middleware.
"""

import os
import django
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
from django.urls import path

# Set Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Initialize Django before importing apps
django.setup()

# Import after Django setup
from chat.routing import websocket_urlpatterns

# ASGI application
application = ProtocolTypeRouter({
    # HTTP handler
    "http": get_asgi_application(),
    
    # WebSocket handler with security and auth
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        )
    ),
})

# Lifespan events for graceful shutdown
from contextlib import asynccontextmanager
import signal
import asyncio
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    """Handle application lifespan events."""
    # Startup
    logger.info("Application starting up...")
    
    # Set readiness flag
    from chat.metrics import app_ready, app_healthy
    app_ready.set(1)
    app_healthy.set(1)
    
    yield
    
    # Shutdown
    logger.info("Application shutting down...")
    app_ready.set(0)
    
    # Trigger graceful shutdown of WebSocket connections
    from chat.consumers import ChatConsumer
    await ChatConsumer.graceful_shutdown()
    
    # Final cleanup
    await asyncio.sleep(0.5)  # Allow final messages to send
    logger.info("Application shutdown complete")


# Wrap application with lifespan handler if using Uvicorn
if os.environ.get('ASGI_LIFESPAN', 'true').lower() == 'true':
    from asgiref.typing import ASGI3Application
    
    class LifespanApp:
        def __init__(self, app: ASGI3Application):
            self.app = app
            
        async def __call__(self, scope, receive, send):
            if scope["type"] == "lifespan":
                async with lifespan(self):
                    while True:
                        message = await receive()
                        if message["type"] == "lifespan.startup":
                            await send({"type": "lifespan.startup.complete"})
                        elif message["type"] == "lifespan.shutdown":
                            await send({"type": "lifespan.shutdown.complete"})
                            break
            else:
                await self.app(scope, receive, send)
    
    application = LifespanApp(application)
