"""
URL configuration for Django WebSocket application.
Includes health checks, metrics, and admin.
"""

from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
import time

from chat.metrics import metrics_view, app_ready, app_healthy


@never_cache
def health_check(request):
    """Liveness probe - basic health check."""
    try:
        # Check if application is responsive
        is_healthy = app_healthy.collect()[0].samples[0].value == 1
        
        if is_healthy:
            return JsonResponse({
                'status': 'healthy',
                'timestamp': time.time(),
                'service': 'django-websocket'
            })
        else:
            return JsonResponse({
                'status': 'unhealthy',
                'timestamp': time.time()
            }, status=503)
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'error': str(e),
            'timestamp': time.time()
        }, status=503)


@never_cache
def readiness_check(request):
    """Readiness probe - check if app is ready to serve traffic."""
    try:
        # Check various components
        checks = {
            'app_ready': False,
            'database': False,
            'redis': False,
            'static_files': False
        }
        
        # Check app readiness flag
        checks['app_ready'] = app_ready.collect()[0].samples[0].value == 1
        
        # Check database connectivity
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            checks['database'] = True
            
        # Check Redis connectivity
        from django.core.cache import cache
        cache.set('readiness_check', 'ok', 1)
        checks['redis'] = cache.get('readiness_check') == 'ok'
        
        # Check static files
        from django.conf import settings
        checks['static_files'] = settings.STATIC_ROOT.exists()
        
        all_ready = all(checks.values())
        
        return JsonResponse({
            'ready': all_ready,
            'checks': checks,
            'timestamp': time.time()
        }, status=200 if all_ready else 503)
        
    except Exception as e:
        return JsonResponse({
            'ready': False,
            'error': str(e),
            'timestamp': time.time()
        }, status=503)


@csrf_exempt
def echo_test(request):
    """Simple echo endpoint for testing."""
    return JsonResponse({
        'method': request.method,
        'path': request.path,
        'headers': dict(request.headers),
        'timestamp': time.time()
    })


urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),
    
    # Health checks
    path('healthz', health_check, name='health'),
    path('readyz', readiness_check, name='readiness'),
    path('health', health_check),  # Alternative health endpoint
    
    # Metrics
    path('metrics', metrics_view, name='metrics'),
    
    # Test endpoint
    path('echo', echo_test, name='echo'),
    
    # Prometheus django metrics
    path('', include('django_prometheus.urls')),
]

# Add debug toolbar in development
from django.conf import settings
if settings.DEBUG:
    try:
        import debug_toolbar
        urlpatterns = [
            path('__debug__/', include(debug_toolbar.urls)),
        ] + urlpatterns
    except ImportError:
        pass
