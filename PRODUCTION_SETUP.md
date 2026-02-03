# RAGFlow Production Setup

## Architecture

```
User Request → Pipeline (Process → Main) → Response
                     ↓
              Weekly: Judge Agent → Evaluation Report
```

## Agent IDs

| Agent | ID | Purpose |
|-------|-----|---------|
| Pipeline: Process-Main | `820dfb2300bf11f18445e224cadbab2f` | Main query handler |
| Process | `35756e8bfb6d11f081a03627fe966451` | Input processing |
| Main | `a0aaa8d1fc2611f0ab2ba6f4b3787fc9` | RAG responses |
| RAG Judge | `383dade900bd11f1b85ae224cadbab2f` | Weekly evaluation |

## 1. Production Docker Setup

```bash
cd /home/edgelab/ragflow/docker

# Edit production environment
cp .env .env.production
```

Edit `.env.production`:
```env
# Production settings
RAGFLOW_VERSION=v0.23.1
HOST_IP=your.server.ip

# Memory limits
ES_HEAP_SIZE=4g
MYSQL_MAX_CONNECTIONS=900

# Security
MYSQL_PASSWORD=<strong-password>
MINIO_PASSWORD=<strong-password>
REDIS_PASSWORD=<strong-password>

# SSL (if using)
SSL_ENABLED=true
```

## 2. Start Production Stack

```bash
docker compose -f docker-compose.yml --env-file .env.production up -d
```

## 3. API Access

### Get API Key
1. Login to RAGFlow UI
2. Go to Settings → API Keys
3. Create new API key

### Query the Pipeline
```bash
# Using the Pipeline agent
curl -X POST "http://your-server:9380/api/v1/agents/820dfb2300bf11f18445e224cadbab2f/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Your query here",
    "stream": false
  }'
```

### Streaming Response
```bash
curl -X POST "http://your-server:9380/api/v1/agents/820dfb2300bf11f18445e224cadbab2f/completions" \
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
0 0 * * 0 /home/edgelab/ragflow/scripts/run_judge_weekly.sh

# Or daily at 2 AM
0 2 * * * /home/edgelab/ragflow/scripts/run_judge_weekly.sh
```

Update the script with API key:
```bash
vi /home/edgelab/ragflow/scripts/run_judge_weekly.sh
```

Add authorization header:
```bash
curl -X POST "${RAGFLOW_HOST}/api/v1/agents/${JUDGE_AGENT_ID}/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  ...
```

## 5. Nginx Reverse Proxy (Optional)

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:80;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /api/ {
        proxy_pass http://localhost:9380/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 6. Monitoring

### Check Container Health
```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
```

### View Logs
```bash
# All logs
docker logs -f docker-ragflow-gpu-1

# Filter agent activity
docker logs -f docker-ragflow-gpu-1 2>&1 | grep -v heartbeat

# Check Judge evaluation logs
tail -f /home/edgelab/ragflow/logs/judge_weekly_*.log
```

### Health Check Endpoint
```bash
curl http://localhost:9380/api/v1/system/version
```

## 7. Backup

### Database Backup
```bash
docker exec docker-mysql-1 mysqldump -u root -p rag_flow > backup_$(date +%Y%m%d).sql
```

### MinIO Backup
```bash
docker run --rm -v ragflow_minio_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/minio_backup_$(date +%Y%m%d).tar.gz /data
```

## 8. Scaling Considerations

- **Task Executors**: Increase `TASK_EXECUTOR_COUNT` in docker-compose for parallel processing
- **Elasticsearch**: Add more nodes for larger knowledge bases
- **Redis**: Enable clustering for high availability

## 9. Integration Example (Python)

```python
import requests

RAGFLOW_URL = "http://your-server:9380"
API_KEY = "your-api-key"
PIPELINE_ID = "820dfb2300bf11f18445e224cadbab2f"

def query_rag(question: str) -> str:
    response = requests.post(
        f"{RAGFLOW_URL}/api/v1/agents/{PIPELINE_ID}/completions",
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

## 10. Troubleshooting

| Issue | Solution |
|-------|----------|
| Agent not responding | Check `docker logs docker-ragflow-gpu-1` |
| Slow responses | Increase `top_k`, check Elasticsearch health |
| Empty results | Lower `similarity_threshold`, check KB content |
| Judge fails | Verify KB IDs in RAGExecutor node |
