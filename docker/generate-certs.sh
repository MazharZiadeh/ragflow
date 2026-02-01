#!/usr/bin/env bash
# Generate self-signed TLS certificates for RAGFlow nginx
set -euo pipefail

CERT_DIR="$(dirname "$0")/nginx/ssl"
mkdir -p "$CERT_DIR"

if [ -f "$CERT_DIR/fullchain.pem" ] && [ -f "$CERT_DIR/privkey.pem" ]; then
    echo "Certificates already exist in $CERT_DIR, skipping generation."
    echo "Delete them and re-run to regenerate."
    exit 0
fi

openssl req -x509 -nodes -days 3650 \
    -newkey rsa:2048 \
    -keyout "$CERT_DIR/privkey.pem" \
    -out "$CERT_DIR/fullchain.pem" \
    -subj "/CN=ragflow/O=RAGFlow/C=US" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

echo "Self-signed certificates generated in $CERT_DIR"
