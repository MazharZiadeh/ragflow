#!/bin/bash
# RAGFlow Production Restore Script
# Restores MySQL database and MinIO data from backup
# Usage: ./restore_ragflow.sh <backup_path>
# WARNING: This will overwrite existing data!

set -euo pipefail

# Configuration
BACKUP_PATH="${1:-}"

# Container names
MYSQL_CONTAINER="mysql"
MINIO_CONTAINER="minio"

# Colors
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

# Validate input
if [ -z "${BACKUP_PATH}" ]; then
    log_error "Usage: $0 <backup_path>"
    log_error "Example: $0 /ragflow/backups/ragflow_backup_20240115_120000"
    exit 1
fi

if [ ! -d "${BACKUP_PATH}" ]; then
    log_error "Backup path does not exist: ${BACKUP_PATH}"
    exit 1
fi

# Check manifest
if [ -f "${BACKUP_PATH}/manifest.json" ]; then
    log_info "Backup manifest found:"
    cat "${BACKUP_PATH}/manifest.json"
    echo
fi

# Confirmation prompt
echo -e "${RED}WARNING: This will overwrite existing data!${NC}"
echo -n "Are you sure you want to restore from ${BACKUP_PATH}? (yes/no): "
read -r CONFIRM

if [ "${CONFIRM}" != "yes" ]; then
    log_info "Restore cancelled"
    exit 0
fi

# Restore MySQL
restore_mysql() {
    log_info "Starting MySQL restore..."

    MYSQL_BACKUP=$(find "${BACKUP_PATH}" -name "mysql_*.sql.gz" -type f | head -1)

    if [ -z "${MYSQL_BACKUP}" ]; then
        log_warn "No MySQL backup found, skipping..."
        return 0
    fi

    if ! docker ps --format '{{.Names}}' | grep -q "^${MYSQL_CONTAINER}$"; then
        log_error "MySQL container '${MYSQL_CONTAINER}' not running"
        return 1
    fi

    MYSQL_USER="${MYSQL_USER:-root}"
    MYSQL_PASSWORD="${MYSQL_PASSWORD:-infini_rag_flow}"
    MYSQL_DATABASE="${MYSQL_DATABASE:-rag_flow}"

    log_info "Restoring from: ${MYSQL_BACKUP}"

    # Decompress and restore
    gunzip -c "${MYSQL_BACKUP}" | docker exec -i "${MYSQL_CONTAINER}" mysql \
        -u"${MYSQL_USER}" \
        -p"${MYSQL_PASSWORD}" \
        "${MYSQL_DATABASE}"

    log_info "MySQL restore completed"
}

# Restore MinIO
restore_minio() {
    log_info "Starting MinIO restore..."

    MINIO_BACKUP="${BACKUP_PATH}/minio_data.tar.gz"
    MINIO_BACKUP_DIR="${BACKUP_PATH}/minio"

    if [ ! -f "${MINIO_BACKUP}" ] && [ ! -d "${MINIO_BACKUP_DIR}" ]; then
        log_warn "No MinIO backup found, skipping..."
        return 0
    fi

    if ! docker ps --format '{{.Names}}' | grep -q "^${MINIO_CONTAINER}$"; then
        log_error "MinIO container '${MINIO_CONTAINER}' not running"
        return 1
    fi

    if [ -f "${MINIO_BACKUP}" ]; then
        # Restore from tar.gz
        MINIO_VOLUME=$(docker inspect "${MINIO_CONTAINER}" --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}')

        if [ -n "${MINIO_VOLUME}" ]; then
            log_info "Restoring MinIO to volume: ${MINIO_VOLUME}"
            tar -xzf "${MINIO_BACKUP}" -C "$(dirname ${MINIO_VOLUME})"
        else
            log_warn "Could not find MinIO volume, using docker cp"
            TMP_DIR=$(mktemp -d)
            tar -xzf "${MINIO_BACKUP}" -C "${TMP_DIR}"
            docker cp "${TMP_DIR}/." "${MINIO_CONTAINER}:/data/"
            rm -rf "${TMP_DIR}"
        fi
    elif [ -d "${MINIO_BACKUP_DIR}" ]; then
        # Restore from directory
        docker cp "${MINIO_BACKUP_DIR}/data/." "${MINIO_CONTAINER}:/data/"
    fi

    log_info "MinIO restore completed"
}

# Restore configuration
restore_config() {
    log_info "Restoring configuration..."

    CONFIG_DIR="${BACKUP_PATH}/config"

    if [ ! -d "${CONFIG_DIR}" ]; then
        log_warn "No configuration backup found, skipping..."
        return 0
    fi

    RAGFLOW_DIR="$(dirname "$(dirname "$(readlink -f "$0")")")"

    if [ -f "${CONFIG_DIR}/.env" ]; then
        log_info "Restoring .env file..."
        cp "${CONFIG_DIR}/.env" "${RAGFLOW_DIR}/docker/.env.restored"
        log_info "Saved as .env.restored (manual merge recommended)"
    fi

    if [ -f "${CONFIG_DIR}/service_conf.yaml" ]; then
        log_info "Restoring service_conf.yaml..."
        cp "${CONFIG_DIR}/service_conf.yaml" "${RAGFLOW_DIR}/docker/service_conf.yaml.restored"
        log_info "Saved as service_conf.yaml.restored (manual merge recommended)"
    fi

    log_info "Configuration restore completed"
}

# Main execution
main() {
    log_info "=========================================="
    log_info "RAGFlow Restore Started"
    log_info "Backup: ${BACKUP_PATH}"
    log_info "=========================================="

    FAILED=0

    restore_mysql || { log_error "MySQL restore failed"; FAILED=1; }
    restore_minio || { log_error "MinIO restore failed"; FAILED=1; }
    restore_config || { log_error "Config restore failed"; FAILED=1; }

    log_info "=========================================="
    if [ ${FAILED} -eq 0 ]; then
        log_info "Restore completed successfully!"
        log_info "Please restart RAGFlow services:"
        log_info "  docker compose restart"
    else
        log_error "Restore completed with failures"
    fi
    log_info "=========================================="

    exit ${FAILED}
}

main "$@"
