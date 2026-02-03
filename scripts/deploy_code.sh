#!/bin/bash
# RAGFlow Code Deployment Script
# Deploys local code changes to the Docker container
# Usage: ./deploy_code.sh [--no-restart]
#
# This script copies modified Python files to the container and restarts it.
# Use --no-restart to copy files without restarting (for batch deployments).

set -euo pipefail

# Configuration
CONTAINER="${RAGFLOW_CONTAINER:-docker-ragflow-gpu-1}"
PROJECT_ROOT="/home/edgelab/ragflow"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

# Check if container is running
check_container() {
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
        log_error "Container ${CONTAINER} is not running"
        exit 1
    fi
    log_info "Container ${CONTAINER} is running"
}

# Copy a file to the container
copy_file() {
    local src="$1"
    local dest="$2"

    if [[ ! -f "$src" ]]; then
        log_warn "Source file not found: $src"
        return 1
    fi

    log_info "Copying $(basename "$src") to container..."
    docker cp "$src" "${CONTAINER}:${dest}"
}

# Main deployment
main() {
    local restart=true

    # Parse arguments
    for arg in "$@"; do
        case $arg in
            --no-restart)
                restart=false
                ;;
        esac
    done

    log_info "Starting RAGFlow code deployment"
    check_container

    # Files to deploy - add more as needed
    declare -A FILES_TO_DEPLOY=(
        ["${PROJECT_ROOT}/agent/component/agent_with_tools.py"]="/ragflow/agent/component/agent_with_tools.py"
        ["${PROJECT_ROOT}/agent/component/llm.py"]="/ragflow/agent/component/llm.py"
        ["${PROJECT_ROOT}/api/apps/sdk/doc.py"]="/ragflow/api/apps/sdk/doc.py"
    )

    local copied=0
    for src in "${!FILES_TO_DEPLOY[@]}"; do
        dest="${FILES_TO_DEPLOY[$src]}"
        if copy_file "$src" "$dest"; then
            ((copied++))
        fi
    done

    log_info "Copied ${copied} file(s) to container"

    if [[ "$restart" == true ]]; then
        log_info "Restarting container ${CONTAINER}..."
        docker restart "${CONTAINER}"
        log_info "Container restarted successfully"

        # Wait for container to be healthy
        log_info "Waiting for container to start..."
        sleep 10

        # Check if container is still running
        if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
            log_info "Deployment completed successfully"
        else
            log_error "Container failed to start after restart"
            exit 1
        fi
    else
        log_info "Skipping restart (use without --no-restart to restart)"
    fi
}

main "$@"
