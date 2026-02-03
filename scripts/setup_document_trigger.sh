#!/bin/bash
# Setup Process Agent to trigger automatically on document upload
# This links the Process agent to a knowledge base so it runs when documents are added

set -euo pipefail

RAGFLOW_HOST="${RAGFLOW_HOST:-http://localhost:9380}"
API_KEY="${RAGFLOW_API_KEY:-}"
PROCESS_AGENT_ID="${PROCESS_AGENT_ID:-35756e8bfb6d11f081a03627fe966451}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

usage() {
    echo "Usage: $0 <knowledge_base_id>"
    echo ""
    echo "Links the Process agent to a knowledge base so it runs automatically"
    echo "when new documents are uploaded."
    echo ""
    echo "Environment variables:"
    echo "  RAGFLOW_API_KEY     - Required: Your RAGFlow API key"
    echo "  RAGFLOW_HOST        - Optional: RAGFlow server URL (default: http://localhost:9380)"
    echo "  PROCESS_AGENT_ID    - Optional: Process agent ID (default: 35756e8bfb6d11f081a03627fe966451)"
    echo ""
    echo "Example:"
    echo "  RAGFLOW_API_KEY=your-key $0 abc123def456"
    exit 1
}

# Check arguments
if [ -z "${1:-}" ]; then
    usage
fi

KB_ID="$1"

# Check API key
if [ -z "${API_KEY}" ]; then
    log_error "RAGFLOW_API_KEY environment variable is required"
    exit 1
fi

log_info "Setting up document upload trigger..."
log_info "Knowledge Base ID: ${KB_ID}"
log_info "Process Agent ID: ${PROCESS_AGENT_ID}"

# Update knowledge base to use Process agent as pipeline
RESPONSE=$(curl -s -X PUT "${RAGFLOW_HOST}/api/v1/knowledgebases/${KB_ID}" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"pipeline_id\": \"${PROCESS_AGENT_ID}\"}")

# Check response
if echo "$RESPONSE" | grep -q '"code":0'; then
    log_info "Success! Process agent linked to knowledge base."
    log_info ""
    log_info "How it works now:"
    log_info "  1. Upload document to KB ${KB_ID}"
    log_info "  2. Process agent (${PROCESS_AGENT_ID}) runs automatically"
    log_info "  3. Document is processed and indexed"
    log_info ""
    log_info "For queries, call Main agent directly:"
    log_info "  POST ${RAGFLOW_HOST}/api/v1/agents/a0aaa8d1fc2611f0ab2ba6f4b3787fc9/completions"
else
    log_error "Failed to update knowledge base"
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    exit 1
fi
