#!/bin/bash
# Blue-Green Deployment Promotion Script
# Handles zero-downtime deployment with health checks and rollback

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
COMPOSE_FILE="${COMPOSE_FILE:-docker/compose.yml}"
COMPOSE_BLUE_GREEN="${COMPOSE_BLUE_GREEN:-docker/compose.blue-green.yml}"
HEALTH_CHECK_RETRIES=${HEALTH_CHECK_RETRIES:-30}
HEALTH_CHECK_DELAY=${HEALTH_CHECK_DELAY:-2}
SMOKE_TEST_TIMEOUT=${SMOKE_TEST_TIMEOUT:-60}

# Functions
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Determine current active color
get_active_color() {
    if docker ps --format "table {{.Names}}" | grep -q "django_ws_blue"; then
        echo "blue"
    else
        echo "green"
    fi
}

# Determine target color
get_target_color() {
    local active_color=$(get_active_color)
    if [ "$active_color" == "blue" ]; then
        echo "green"
    else
        echo "blue"
    fi
}

# Check if a container is healthy
check_container_health() {
    local container_name=$1
    local health_status=$(docker inspect --format='{{.State.Health.Status}}' "$container_name" 2>/dev/null || echo "unknown")
    
    if [ "$health_status" == "healthy" ]; then
        return 0
    else
        return 1
    fi
}

# Wait for container to be ready
wait_for_ready() {
    local container_name=$1
    local retries=$HEALTH_CHECK_RETRIES
    
    log "Waiting for $container_name to be ready..."
    
    while [ $retries -gt 0 ]; do
        if check_container_health "$container_name"; then
            # Additional readiness check via HTTP
            if docker exec "$container_name" curl -sf http://localhost:8000/readyz > /dev/null 2>&1; then
                success "$container_name is ready!"
                return 0
            fi
        fi
        
        retries=$((retries - 1))
        if [ $retries -gt 0 ]; then
            echo -n "."
            sleep $HEALTH_CHECK_DELAY
        fi
    done
    
    error "$container_name failed to become ready"
    return 1
}

# Run smoke tests
run_smoke_tests() {
    local target_color=$1
    local target_port=$2
    
    log "Running smoke tests against $target_color environment..."
    
    # Basic connectivity test
    if ! curl -sf "http://localhost:$target_port/healthz" > /dev/null; then
        error "Health check failed"
        return 1
    fi
    
    # WebSocket test
    python3 scripts/smoke_test.py --url "ws://localhost:$target_port/ws/chat/" --timeout $SMOKE_TEST_TIMEOUT
    
    # Metrics endpoint test
    if ! curl -sf "http://localhost:$target_port/metrics" | grep -q "websocket_connections_active"; then
        error "Metrics endpoint check failed"
        return 1
    fi
    
    success "All smoke tests passed!"
    return 0
}

# Update nginx configuration
update_nginx_config() {
    local target_color=$1
    local target_port=$2
    
    log "Updating nginx configuration to route to $target_color..."
    
    # Generate new nginx config
    cat > docker/nginx.conf.new <<EOF
upstream django_app {
    server app_${target_color}:${target_port} max_fails=3 fail_timeout=30s;
}

server {
    listen 80;
    server_name localhost;
    
    location / {
        proxy_pass http://django_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    
    location /ws/ {
        proxy_pass http://django_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    
    location /static/ {
        alias /static/;
    }
    
    location /health {
        access_log off;
        return 200 "healthy\n";
    }
}
EOF
    
    # Atomic move
    mv docker/nginx.conf.new docker/nginx.conf
    
    # Reload nginx
    docker-compose -f "$COMPOSE_FILE" exec -T nginx nginx -s reload
    
    success "Nginx configuration updated"
}

# Main promotion logic
main() {
    log "Starting blue-green deployment promotion..."
    
    # Parse arguments
    local force_color="${1:-}"
    
    # Determine colors
    local active_color=$(get_active_color)
    local target_color
    
    if [ -n "$force_color" ]; then
        target_color="$force_color"
    else
        target_color=$(get_target_color)
    fi
    
    local active_port=$([[ "$active_color" == "blue" ]] && echo "8001" || echo "8002")
    local target_port=$([[ "$target_color" == "blue" ]] && echo "8001" || echo "8002")
    
    log "Current active: $active_color (port $active_port)"
    log "Target deployment: $target_color (port $target_port)"
    
    # Step 1: Build and start target environment
    log "Building and starting $target_color environment..."
    docker-compose -f "$COMPOSE_FILE" -f "$COMPOSE_BLUE_GREEN" build "app_${target_color}"
    docker-compose -f "$COMPOSE_FILE" -f "$COMPOSE_BLUE_GREEN" up -d "app_${target_color}"
    
    # Step 2: Wait for target to be ready
    if ! wait_for_ready "django_ws_${target_color}"; then
        error "Target environment failed to start"
        log "Rolling back..."
        docker-compose -f "$COMPOSE_FILE" stop "app_${target_color}"
        exit 1
    fi
    
    # Step 3: Run smoke tests
    if ! run_smoke_tests "$target_color" "$target_port"; then
        error "Smoke tests failed"
        log "Rolling back..."
        docker-compose -f "$COMPOSE_FILE" stop "app_${target_color}"
        exit 1
    fi
    
    # Step 4: Update load balancer
    update_nginx_config "$target_color" "$target_port"
    
    # Step 5: Wait for traffic to stabilize
    log "Waiting for traffic to stabilize..."
    sleep 5
    
    # Step 6: Graceful shutdown of old environment
    log "Initiating graceful shutdown of $active_color environment..."
    docker-compose -f "$COMPOSE_FILE" exec -T "app_${active_color}" kill -TERM 1
    
    # Wait for graceful shutdown (max 10 seconds)
    local shutdown_wait=10
    while [ $shutdown_wait -gt 0 ] && docker ps | grep -q "django_ws_${active_color}"; do
        sleep 1
        shutdown_wait=$((shutdown_wait - 1))
    done
    
    # Step 7: Stop old environment
    log "Stopping $active_color environment..."
    docker-compose -f "$COMPOSE_FILE" stop "app_${active_color}"
    
    success "Blue-green deployment completed successfully!"
    log "Active environment: $target_color"
    
    # Optional: Clean up old images
    if [ "${CLEANUP_OLD_IMAGES:-false}" == "true" ]; then
        log "Cleaning up old Docker images..."
        docker image prune -f
    fi
}

# Handle script interruption
trap 'error "Deployment interrupted"; exit 1' INT TERM

# Run main function
main "$@"
