# RAGFlow Production Setup

## Architecture

```
Document Upload → Process Agent → Index to KB
                       ↑
                  (automatic trigger)

User Query → Main Agent → Response
                  ↓
           Weekly: Judge Agent → Evaluation Report
```

**Key Design:**
- **Process Agent**: Runs automatically when documents are uploaded (via KB pipeline trigger)
- **Main Agent**: Handles all user queries (RAG retrieval + response)
- **Judge Agent**: Runs weekly to evaluate RAG quality

## Agent IDs

| Agent | ID | Trigger | Purpose |
|-------|-----|---------|---------|
| Process | `35756e8bfb6d11f081a03627fe966451` | Document upload | Document processing & indexing |
| Main | `a0aaa8d1fc2611f0ab2ba6f4b3787fc9` | User query | RAG retrieval & responses |
| RAG Judge | `383dade900bd11f1b85ae224cadbab2f` | Weekly cron | Quality evaluation |

## 1. Setup Document Processing Trigger

Link Process agent to your knowledge base so it runs automatically on document upload:

```bash
# Set your API key
export RAGFLOW_API_KEY="your-api-key"

# Link Process agent to knowledge base
./scripts/setup_document_trigger.sh <your-kb-id>
```

Or manually via API:
```bash
curl -X PUT "http://localhost:9380/api/v1/datasets/<kb-id>" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"pipeline_id": "35756e8bfb6d11f081a03627fe966451"}'
```

## 2. Production Docker Setup

```bash
cd /home/edgelab/ragflow/docker

# Start with production overlay (includes monitoring)
docker compose -f docker-compose.yml -f docker-compose-production.yml --profile gpu up -d
```

### Environment Configuration

Edit `.env` for production:
```env
# Production settings
RAGFLOW_VERSION=v0.23.1
HOST_IP=your.server.ip

# Memory limits
ES_HEAP_SIZE=4g
MYSQL_MAX_CONNECTIONS=900

# Security - change these!
MYSQL_PASSWORD=<strong-password>
MINIO_PASSWORD=<strong-password>
REDIS_PASSWORD=<strong-password>
```

## 3. API Access

### Get API Key
1. Login to RAGFlow UI (http://localhost:80)
2. Go to Settings → API Keys
3. Create new API key

### Query the Main Agent

```bash
# Standard query
curl -X POST "http://localhost:9380/api/v1/agents/a0aaa8d1fc2611f0ab2ba6f4b3787fc9/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Your query here",
    "stream": false
  }'
```

### Streaming Response

```bash
curl -X POST "http://localhost:9380/api/v1/agents/a0aaa8d1fc2611f0ab2ba6f4b3787fc9/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Your query here",
    "stream": true
  }'
```

## 4. Weekly Judge Cron Setup

```bash
# Edit crontab
crontab -e

# Add weekly evaluation (Sundays at midnight)
0 0 * * 0 RAGFLOW_API_KEY=your-key /home/edgelab/ragflow/scripts/run_judge_weekly.sh >> /home/edgelab/ragflow/logs/judge.log 2>&1
```

## 5. Backup Automation

### Daily Backup Cron

```bash
# Add to crontab (daily at 2 AM)
0 2 * * * MYSQL_PASSWORD="your-mysql-password" /home/edgelab/ragflow/scripts/backup_ragflow.sh /home/edgelab/ragflow/backups >> /home/edgelab/ragflow/logs/backup.log 2>&1
```

### Manual Backup

```bash
MYSQL_PASSWORD="your-password" ./scripts/backup_ragflow.sh /path/to/backups
```

### Restore from Backup

```bash
./scripts/restore_ragflow.sh /path/to/backup/ragflow_backup_20260203_083254
```

## 6. Monitoring

### Monitoring Stack (included in production compose)

| Service | URL | Credentials |
|---------|-----|-------------|
| Prometheus | http://localhost:9090 | - |
| Grafana | http://localhost:3001 | admin / ragflow123 |
| cAdvisor | http://localhost:8080 | - |

### Check Container Health

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

### View Logs

```bash
# RAGFlow server logs
docker logs -f docker-ragflow-gpu-1

# Filter out heartbeat noise
docker logs -f docker-ragflow-gpu-1 2>&1 | grep -v heartbeat

# Check backup logs
tail -f /home/edgelab/ragflow/logs/backup.log

# Check Judge evaluation logs
tail -f /home/edgelab/ragflow/logs/judge.log
```

### Health Check Endpoint

```bash
curl http://localhost:9380/api/v1/system/version
```

## 7. Nginx Reverse Proxy (Optional)

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # Frontend
    location / {
        proxy_pass http://localhost:80;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # API
    location /api/ {
        proxy_pass http://localhost:9380/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
    }
}
```

## 8. Integration Examples

### Python

```python
import requests

RAGFLOW_URL = "http://your-server:9380"
API_KEY = "your-api-key"
MAIN_AGENT_ID = "a0aaa8d1fc2611f0ab2ba6f4b3787fc9"

def query_rag(question: str) -> dict:
    """Query the Main agent for RAG responses."""
    response = requests.post(
        f"{RAGFLOW_URL}/api/v1/agents/{MAIN_AGENT_ID}/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        json={"question": question, "stream": False}
    )
    return response.json()

# Usage
result = query_rag("What is machine learning?")
print(result)
```

### JavaScript/Node.js

```javascript
const RAGFLOW_URL = "http://your-server:9380";
const API_KEY = "your-api-key";
const MAIN_AGENT_ID = "a0aaa8d1fc2611f0ab2ba6f4b3787fc9";

async function queryRAG(question) {
  const response = await fetch(
    `${RAGFLOW_URL}/api/v1/agents/${MAIN_AGENT_ID}/completions`,
    {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${API_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ question, stream: false })
    }
  );
  return response.json();
}

// Usage
queryRAG("What is machine learning?").then(console.log);
```

## 9. Scaling Considerations

- **Task Executors**: Increase `TASK_EXECUTOR_COUNT` in docker-compose for parallel document processing
- **Elasticsearch**: Add more nodes for larger knowledge bases (>1M chunks)
- **Redis**: Enable clustering for high availability
- **GPU**: Ensure CUDA is available for embedding models

## 10. Troubleshooting

| Issue | Solution |
|-------|----------|
| Process not running on upload | Check KB has `pipeline_id` set, verify via API |
| Main agent not responding | Check `docker logs docker-ragflow-gpu-1` |
| Slow responses | Increase `top_k`, check Elasticsearch health |
| Empty results | Lower `similarity_threshold`, check KB has documents |
| Judge fails | Verify KB IDs in RAGExecutor node, check API key |
| Backup fails | Check MySQL password, ensure docker containers running |

### Verify Process Trigger Setup

```bash
# Check if KB has pipeline_id set
curl -s "http://localhost:9380/api/v1/datasets/<kb-id>" \
  -H "Authorization: Bearer YOUR_API_KEY" | python3 -m json.tool | grep pipeline
```

### Check Document Processing Queue

```bash
# View Redis queue (inside container)
docker exec docker-redis-1 redis-cli LLEN rag_flow_task_queue
```
