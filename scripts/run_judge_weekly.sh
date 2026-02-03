#!/bin/bash
# Weekly RAG Judge Agent runner
# Add to crontab: 0 0 * * 0 /home/edgelab/ragflow/scripts/run_judge_weekly.sh

JUDGE_AGENT_ID="383dade900bd11f1b85ae224cadbab2f"
RAGFLOW_HOST="http://localhost:9380"
API_KEY="${RAGFLOW_API_KEY:-your-api-key-here}"  # Set via environment or replace

LOG_DIR="/home/edgelab/ragflow/logs"
LOG_FILE="${LOG_DIR}/judge_weekly_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

echo "Starting RAG Judge evaluation at $(date)" | tee "$LOG_FILE"

# Trigger the Judge agent
curl -X POST "${RAGFLOW_HOST}/api/v1/agents/${JUDGE_AGENT_ID}/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"question": "Run weekly RAG evaluation", "stream": false}' \
  >> "$LOG_FILE" 2>&1

echo "" >> "$LOG_FILE"
echo "Judge evaluation completed at $(date)" | tee -a "$LOG_FILE"
