"""
Prometheus metrics configuration for WebSocket monitoring.
Tracks connections, messages, errors, and performance metrics.
"""

from prometheus_client import Counter, Gauge, Histogram, Summary
from prometheus_client.core import CollectorRegistry
from prometheus_client.multiprocess import MultiProcessCollector

# Create a custom registry for multiprocess mode
registry = CollectorRegistry()
MultiProcessCollector(registry)

# WebSocket connection metrics
websocket_connections = Gauge(
    'websocket_connections_active',
    'Number of active WebSocket connections',
    registry=registry
)

websocket_connections_total = Counter(
    'websocket_connections_total',
    'Total number of WebSocket connections',
    registry=registry
)

# Message metrics
websocket_messages = Counter(
    'websocket_messages_total',
    'Total number of WebSocket messages processed',
    registry=registry
)

websocket_message_size = Histogram(
    'websocket_message_size_bytes',
    'Size of WebSocket messages in bytes',
    buckets=(10, 50, 100, 500, 1000, 5000, 10000),
    registry=registry
)

# Error metrics
websocket_errors = Counter(
    'websocket_errors_total',
    'Total number of WebSocket errors',
    ['error_type'],
    registry=registry
)

# Performance metrics
websocket_message_duration = Histogram(
    'websocket_message_duration_seconds',
    'Time to process WebSocket messages',
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
    registry=registry
)

websocket_connection_duration = Histogram(
    'websocket_connection_duration_seconds',
    'Duration of WebSocket connections',
    buckets=(1, 10, 60, 300, 600, 1800, 3600, 7200),
    registry=registry
)

# Heartbeat metrics
websocket_heartbeats = Counter(
    'websocket_heartbeats_total',
    'Total number of heartbeats sent',
    registry=registry
)

# Shutdown metrics
websocket_shutdown_duration = Histogram(
    'websocket_shutdown_duration_seconds',
    'Time taken for graceful shutdown',
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0),
    registry=registry
)

# Reconnection metrics
websocket_reconnections = Counter(
    'websocket_reconnections_total',
    'Total number of successful reconnections',
    registry=registry
)

# System health metrics
app_ready = Gauge(
    'app_ready',
    'Application readiness status (1=ready, 0=not ready)',
    registry=registry
)

app_healthy = Gauge(
    'app_healthy',
    'Application health status (1=healthy, 0=unhealthy)',
    registry=registry
)

# Django request metrics
django_requests = Counter(
    'django_requests_total',
    'Total Django HTTP requests',
    ['method', 'endpoint', 'status'],
    registry=registry
)

django_request_duration = Histogram(
    'django_request_duration_seconds',
    'Django HTTP request duration',
    ['method', 'endpoint'],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry
)

# Database metrics
db_connections_active = Gauge(
    'db_connections_active',
    'Number of active database connections',
    registry=registry
)

db_queries = Counter(
    'db_queries_total',
    'Total number of database queries',
    ['operation'],
    registry=registry
)

# Redis metrics
redis_connections_active = Gauge(
    'redis_connections_active',
    'Number of active Redis connections',
    registry=registry
)

redis_operations = Counter(
    'redis_operations_total',
    'Total Redis operations',
    ['operation'],
    registry=registry
)

# Memory metrics
memory_usage_bytes = Gauge(
    'memory_usage_bytes',
    'Process memory usage in bytes',
    ['type'],
    registry=registry
)


# Helper functions
def track_websocket_duration(duration: float):
    """Track WebSocket connection duration."""
    websocket_connection_duration.observe(duration)


def track_message_size(size: int):
    """Track WebSocket message size."""
    websocket_message_size.observe(size)


def track_request(method: str, endpoint: str, status: int, duration: float):
    """Track Django HTTP request."""
    django_requests.labels(method=method, endpoint=endpoint, status=str(status)).inc()
    django_request_duration.labels(method=method, endpoint=endpoint).observe(duration)


# Metrics view for /metrics endpoint
from django.http import HttpResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST


def metrics_view(request):
    """Expose metrics for Prometheus scraping."""
    metrics = generate_latest(registry)
    return HttpResponse(metrics, content_type=CONTENT_TYPE_LATEST)


# Middleware for automatic request tracking
import time
from django.utils.deprecation import MiddlewareMixin


class PrometheusMiddleware(MiddlewareMixin):
    """Middleware to track Django requests automatically."""
    
    def process_request(self, request):
        request._prometheus_start_time = time.time()
        
    def process_response(self, request, response):
        if hasattr(request, '_prometheus_start_time'):
            duration = time.time() - request._prometheus_start_time
            track_request(
                method=request.method,
                endpoint=request.path,
                status=response.status_code,
                duration=duration
            )
        return response
