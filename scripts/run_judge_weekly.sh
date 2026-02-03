#!/bin/bash
# Weekly RAG Judge Agent runner
# Add to crontab: 0 0 * * 0 /home/edgelab/ragflow/scripts/run_judge_weekly.sh

JUDGE_AGENT_ID="383dade900bd11f1b85ae224cadbab2f"
RAGFLOW_HOST="http://localhost:9380"

# Trigger the Judge agent
curl -X POST "${RAGFLOW_HOST}/api/v1/agents/${JUDGE_AGENT_ID}/completions" \
  -H "Content-Type: application/json" \
  -d '{"question": "Run weekly RAG evaluation", "stream": false}' \
  > /home/edgelab/ragflow/logs/judge_weekly_$(date +%Y%m%d).log 2>&1

echo "Judge evaluation completed at $(date)"
