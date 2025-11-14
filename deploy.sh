#!/bin/bash

# Deployment script for Cover Art Archive caching proxy

set -e

# Load environment variables from .env file
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo "Warning: .env file not found. Using default values."
fi

# Configuration
SERVICE_NAME="app"  # Service name in docker-compose.yml
COMPOSE_FILE="docker-compose.yml"
SERVICE_PORT="${SERVICE_PORT:-8000}"  # Default to 8000 if not set

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Docker is installed
check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker and try again."
        exit 1
    fi
    
    log_success "Docker is installed"
}

# Check if service is already running
check_existing() {
    if docker compose ps | grep -q "$SERVICE_NAME"; then
        log_warning "Service is already running. Use './deploy.sh restart' to restart it."
        return 0
    fi
    return 1
}

# Deploy the service
deploy() {
    log_info "Starting deployment of Cover Art Archive caching proxy..."
    
    # Build and start the service
    log_info "Building Docker image..."
    docker compose build
    
    log_info "Starting service..."
    docker compose up -d
    
    # Wait for service to be ready
    log_info "Waiting for service to be ready..."
    sleep 10
    
    # Health check
    if curl -f -s http://localhost:${SERVICE_PORT}/health > /dev/null; then
        log_success "Service is running and healthy!"
        log_info "Access your cache at: http://localhost:${SERVICE_PORT}"
        log_info "Check status at: http://localhost:${SERVICE_PORT}/cache-status"
    else
        log_error "Service health check failed"
        log_info "Check logs with: docker compose logs $SERVICE_NAME"
        exit 1
    fi
}

# Stop the service
stop() {
    log_info "Stopping Cover Art Archive caching proxy..."
    docker compose down
    log_success "Service stopped"
}

# Restart the service
restart() {
    log_info "Restarting Cover Art Archive caching proxy..."
    docker compose down
    docker compose up -d
    
    # Wait for service to be ready
    sleep 10
    
    # Health check
    if curl -f -s http://localhost:${SERVICE_PORT}/health > /dev/null; then
        log_success "Service restarted successfully!"
    else
        log_error "Service health check failed after restart"
        log_info "Check logs with: docker compose logs $SERVICE_NAME"
        exit 1
    fi
}

# Show logs
logs() {
    docker compose logs "$@" $SERVICE_NAME
}

# Show status
status() {
    echo "Service Status:"
    docker compose ps
    echo ""
    
    echo "Cache Size:"
    if [ -n "$COVER_ART_CACHE_DIR" ]; then
        du -sh "$COVER_ART_CACHE_DIR" 2>/dev/null || echo "Cache directory not accessible"
    else
        docker compose exec $SERVICE_NAME du -sh /cache 2>/dev/null || echo "Cache directory not accessible"
    fi
    echo ""
    
    echo "Recent Activity (last 10 lines):"
    docker compose logs --tail=10 $SERVICE_NAME
}

# Clean cache
clean_cache() {
    log_warning "This will delete all cached images. Are you sure? (y/N)"
    read -r response
    if [[ "$response" =~ ^[Yy]$ ]]; then
        log_info "Cleaning cache..."
        docker compose exec $SERVICE_NAME find /cache -type f -delete 2>/dev/null || true
        log_success "Cache cleaned"
    else
        log_info "Cache cleaning cancelled"
    fi
}

# Show help
show_help() {
    echo "Cover Art Archive Caching Proxy Deployment Script"
    echo ""
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  deploy    Deploy the caching proxy (default)"
    echo "  stop      Stop the service"
    echo "  restart   Restart the service"
    echo "  logs      Show service logs (pass -f to follow)"
    echo "  status    Show service status and cache info"
    echo "  clean     Clean the cache (delete all cached images)"
    echo "  test      Run basic functionality tests"
    echo "  help      Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 deploy          # Deploy the service"
    echo "  $0 logs -f         # Follow logs in real-time"
    echo "  $0 status          # Check service status"
    echo "  $0 clean           # Clean the cache"
}

# Run tests
run_tests() {
    log_info "Running functionality tests..."
    if [ -f "./test.sh" ]; then
        ./test.sh
    else
        log_error "test.sh not found"
        exit 1
    fi
}

# Main script
case "${1:-deploy}" in
    deploy)
        check_docker
        if ! check_existing; then
            deploy
        fi
        ;;
    stop)
        stop
        ;;
    restart)
        check_docker
        restart
        ;;
    logs)
        shift
        logs "$@"
        ;;
    status)
        status
        ;;
    clean)
        clean_cache
        ;;
    test)
        run_tests
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
