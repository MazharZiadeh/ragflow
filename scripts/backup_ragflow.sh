#!/bin/bash
# RAGFlow Production Backup Script
# Backs up MySQL database, MinIO data, and Elasticsearch indices
# Usage: ./backup_ragflow.sh [backup_dir]
# Recommended: Run daily via cron

set -euo pipefail

# Configuration
BACKUP_DIR="${1:-/ragflow/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="ragflow_backup_${TIMESTAMP}"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"

# Container names (adjust if different)
MYSQL_CONTAINER="${MYSQL_CONTAINER:-docker-mysql-1}"
MINIO_CONTAINER="${MINIO_CONTAINER:-docker-minio-1}"
ES_CONTAINER="${ES_CONTAINER:-docker-es01-1}"

# Retention (days)
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"

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

# Create backup directory
mkdir -p "${BACKUP_PATH}"
log_info "Created backup directory: ${BACKUP_PATH}"

# Backup MySQL
backup_mysql() {
    log_info "Starting MySQL backup..."

    if ! docker ps --format '{{.Names}}' | grep -q "^${MYSQL_CONTAINER}$"; then
        log_error "MySQL container '${MYSQL_CONTAINER}' not running"
        return 1
    fi

    # Get MySQL credentials from environment or use defaults
    MYSQL_USER="${MYSQL_USER:-root}"
    MYSQL_PASSWORD="${MYSQL_PASSWORD:-infini_rag_flow}"
    MYSQL_DATABASE="${MYSQL_DATABASE:-rag_flow}"

    docker exec "${MYSQL_CONTAINER}" mysqldump \
        -u"${MYSQL_USER}" \
        -p"${MYSQL_PASSWORD}" \
        --single-transaction \
        --routines \
        --triggers \
        "${MYSQL_DATABASE}" > "${BACKUP_PATH}/mysql_${MYSQL_DATABASE}.sql"

    gzip "${BACKUP_PATH}/mysql_${MYSQL_DATABASE}.sql"
    log_info "MySQL backup completed: mysql_${MYSQL_DATABASE}.sql.gz"
}

# Backup MinIO data
backup_minio() {
    log_info "Starting MinIO backup..."

    if ! docker ps --format '{{.Names}}' | grep -q "^${MINIO_CONTAINER}$"; then
        log_error "MinIO container '${MINIO_CONTAINER}' not running"
        return 1
    fi

    # Use docker cp to backup MinIO data (works without root access)
    mkdir -p "${BACKUP_PATH}/minio"
    log_info "Copying MinIO data from container..."
    docker cp "${MINIO_CONTAINER}:/data/." "${BACKUP_PATH}/minio/"

    # Compress the backup
    if [ -d "${BACKUP_PATH}/minio" ] && [ "$(ls -A ${BACKUP_PATH}/minio 2>/dev/null)" ]; then
        tar -czf "${BACKUP_PATH}/minio_data.tar.gz" -C "${BACKUP_PATH}" minio
        rm -rf "${BACKUP_PATH}/minio"
        log_info "MinIO backup completed and compressed"
    else
        log_warn "MinIO data directory is empty"
    fi
}

# Backup Elasticsearch indices
backup_elasticsearch() {
    log_info "Starting Elasticsearch backup..."

    if ! docker ps --format '{{.Names}}' | grep -q "^${ES_CONTAINER}$"; then
        log_error "Elasticsearch container '${ES_CONTAINER}' not running"
        return 1
    fi

    ES_HOST="${ES_HOST:-http://localhost:9200}"

    # Create snapshot repository if not exists
    curl -s -X PUT "${ES_HOST}/_snapshot/backup_repo" \
        -H "Content-Type: application/json" \
        -d "{\"type\": \"fs\", \"settings\": {\"location\": \"/usr/share/elasticsearch/backup\"}}" || true

    # Create snapshot
    SNAPSHOT_NAME="snapshot_${TIMESTAMP}"
    curl -s -X PUT "${ES_HOST}/_snapshot/backup_repo/${SNAPSHOT_NAME}?wait_for_completion=true" \
        -H "Content-Type: application/json" \
        -d '{"indices": "*", "ignore_unavailable": true, "include_global_state": false}'

    # Copy snapshot data from container
    mkdir -p "${BACKUP_PATH}/elasticsearch"
    docker cp "${ES_CONTAINER}:/usr/share/elasticsearch/backup" "${BACKUP_PATH}/elasticsearch/" 2>/dev/null || \
        log_warn "Elasticsearch snapshot copy failed - manual backup may be needed"

    log_info "Elasticsearch backup completed"
}

# Backup RAGFlow configuration
backup_config() {
    log_info "Backing up configuration files..."

    CONFIG_DIR="${BACKUP_PATH}/config"
    mkdir -p "${CONFIG_DIR}"

    # Copy docker compose and env files
    RAGFLOW_DIR="$(dirname "$(dirname "$(readlink -f "$0")")")"

    if [ -f "${RAGFLOW_DIR}/docker/.env" ]; then
        cp "${RAGFLOW_DIR}/docker/.env" "${CONFIG_DIR}/"
    fi

    if [ -f "${RAGFLOW_DIR}/docker/service_conf.yaml" ]; then
        cp "${RAGFLOW_DIR}/docker/service_conf.yaml" "${CONFIG_DIR}/"
    fi

    log_info "Configuration backup completed"
}

# Clean up old backups
cleanup_old_backups() {
    log_info "Cleaning up backups older than ${RETENTION_DAYS} days..."

    find "${BACKUP_DIR}" -maxdepth 1 -type d -name "ragflow_backup_*" -mtime +${RETENTION_DAYS} -exec rm -rf {} \; 2>/dev/null || true

    REMOVED_COUNT=$(find "${BACKUP_DIR}" -maxdepth 1 -type d -name "ragflow_backup_*" -mtime +${RETENTION_DAYS} 2>/dev/null | wc -l)
    log_info "Cleanup completed"
}

# Create backup manifest
create_manifest() {
    log_info "Creating backup manifest..."

    cat > "${BACKUP_PATH}/manifest.json" << EOF
{
    "backup_name": "${BACKUP_NAME}",
    "timestamp": "${TIMESTAMP}",
    "created_at": "$(date -Iseconds)",
    "components": {
        "mysql": $([ -f "${BACKUP_PATH}/mysql_${MYSQL_DATABASE:-rag_flow}.sql.gz" ] && echo "true" || echo "false"),
        "minio": $([ -f "${BACKUP_PATH}/minio_data.tar.gz" ] || [ -d "${BACKUP_PATH}/minio" ] && echo "true" || echo "false"),
        "elasticsearch": $([ -d "${BACKUP_PATH}/elasticsearch" ] && echo "true" || echo "false"),
        "config": $([ -d "${BACKUP_PATH}/config" ] && echo "true" || echo "false")
    },
    "size_bytes": $(du -sb "${BACKUP_PATH}" | cut -f1)
}
EOF

    log_info "Manifest created"
}

# Main execution
main() {
    log_info "=========================================="
    log_info "RAGFlow Backup Started"
    log_info "=========================================="

    FAILED=0

    backup_mysql || { log_error "MySQL backup failed"; FAILED=1; }
    backup_minio || { log_error "MinIO backup failed"; FAILED=1; }
    backup_elasticsearch || { log_error "Elasticsearch backup failed"; FAILED=1; }
    backup_config || { log_error "Config backup failed"; FAILED=1; }

    create_manifest
    cleanup_old_backups

    # Calculate total size
    TOTAL_SIZE=$(du -sh "${BACKUP_PATH}" | cut -f1)

    log_info "=========================================="
    if [ ${FAILED} -eq 0 ]; then
        log_info "Backup completed successfully!"
    else
        log_warn "Backup completed with some failures"
    fi
    log_info "Location: ${BACKUP_PATH}"
    log_info "Total size: ${TOTAL_SIZE}"
    log_info "=========================================="

    exit ${FAILED}
}

main "$@"
