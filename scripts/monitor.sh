#!/bin/bash
# Real-time monitoring script for Django WebSocket application
# Tails logs for errors and displays key metrics

set -euo pipefail

# Configuration
METRICS_URL="${METRICS_URL:-http://localhost:8000/metrics}"
LOG_CONTAINER="${LOG_CONTAINER:-django_ws_blue}"
REFRESH_INTERVAL="${REFRESH_INTERVAL:-10}"
TOP_METRICS_COUNT="${TOP_METRICS_COUNT:-5}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Functions
header() {
    echo -e "\n${BLUE}=== $1 ===${NC}"
}

error_line() {
    echo -e "${RED}[ERROR]${NC} $1"
}

metric_line() {
    local name=$1
    local value=$2
    local unit=${3:-""}
    printf "${CYAN}%-40s${NC} ${GREEN}%10s${NC} %s\n" "$name" "$value" "$unit"
}

# Parse Prometheus metrics
parse_metrics() {
    local metrics_data=$1
    
    # Extract key metrics
    local active_connections=$(echo "$metrics_data" | grep "^websocket_connections_active" | awk '{print $2}' | head -1)
    local total_messages=$(echo "$metrics_data" | grep "^websocket_messages_total" | awk '{print $2}' | head -1)
    local total_errors=$(echo "$metrics_data" | grep "^websocket_errors_total" | awk '{print $2}' | awk '{sum+=$1} END {print sum}')
    local app_ready=$(echo "$metrics_data" | grep "^app_ready" | awk '{print $2}' | head -1)
    local app_healthy=$(echo "$metrics_data" | grep "^app_healthy" | awk '{print $2}' | head -1)
    
    # Display metrics
    header "Application Status"
    metric_line "App Ready" "$([[ "$app_ready" == "1.0" ]] && echo "YES" || echo "NO")"
    metric_line "App Healthy" "$([[ "$app_healthy" == "1.0" ]] && echo "YES" || echo "NO")"
    
    header "WebSocket Metrics"
    metric_line "Active Connections" "${active_connections:-0}"
    metric_line "Total Messages" "${total_messages:-0}"
    metric_line "Total Errors" "${total_errors:-0}"
    
    # Get top metrics by value
    header "Top $TOP_METRICS_COUNT Metrics"
    echo "$metrics_data" | \
        grep -E "^[a-zA-Z_][a-zA-Z0-9_]*{.*}?\s+[0-9]" | \
        sort -k2 -nr | \
        head -n "$TOP_METRICS_COUNT" | \
        while read -r line; do
            local metric_name=$(echo "$line" | awk '{print $1}')
            local metric_value=$(echo "$line" | awk '{print $2}')
            metric_line "$metric_name" "$metric_value"
        done
}

# Monitor logs for errors
monitor_logs() {
    local container=$1
    
    # Start log tail in background
    docker logs -f "$container" 2>&1 | while read -r line; do
        if echo "$line" | grep -iE "(ERROR|CRITICAL|FATAL)" > /dev/null; then
            error_line "$line"
        fi
    done &
    
    LOG_PID=$!
}

# Fetch and display metrics
fetch_metrics() {
    local metrics_data
    
    if metrics_data=$(curl -sf "$METRICS_URL" 2>/dev/null); then
        parse_metrics "$metrics_data"
    else
        error_line "Failed to fetch metrics from $METRICS_URL"
    fi
}

# Main monitoring loop
main() {
    echo -e "${GREEN}Django WebSocket Monitoring Script${NC}"
    echo -e "Monitoring container: ${YELLOW}$LOG_CONTAINER${NC}"
    echo -e "Metrics URL: ${YELLOW}$METRICS_URL${NC}"
    echo -e "Refresh interval: ${YELLOW}${REFRESH_INTERVAL}s${NC}\n"
    
    # Check if container exists
    if ! docker ps --format '{{.Names}}' | grep -q "^${LOG_CONTAINER}$"; then
        error_line "Container $LOG_CONTAINER not found!"
        echo "Available containers:"
        docker ps --format 'table {{.Names}}\t{{.Status}}'
        exit 1
    fi
    
    # Start log monitoring
    header "Starting Log Monitor"
    monitor_logs "$LOG_CONTAINER"
    echo "Tailing logs for ERROR messages..."
    
    # Metrics loop
    while true; do
        # Clear screen for metrics (keep log errors visible)
        printf "\033[10;0H"  # Move cursor to line 10
        
        # Display timestamp
        echo -e "\n${YELLOW}Last updated: $(date '+%Y-%m-%d %H:%M:%S')${NC}"
        
        # Fetch and display metrics
        fetch_metrics
        
        # System metrics
        header "System Metrics"
        
        # Container stats
        local container_stats=$(docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}" | grep "$LOG_CONTAINER" || true)
        if [ -n "$container_stats" ]; then
            echo "$container_stats"
        fi
        
        # Sleep before next update
        sleep "$REFRESH_INTERVAL"
    done
}

# Cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down monitor...${NC}"
    if [ -n "${LOG_PID:-}" ]; then
        kill "$LOG_PID" 2>/dev/null || true
    fi
    exit 0
}

# Set up signal handlers
trap cleanup INT TERM

# Run main function
main

# Wait for background process
wait
