# RAGFlow System Audit

**Generated:** 2026-02-03
**Version:** RAGFlow v0.23.1
**Environment:** NVIDIA DGX Linux Server

---

## 1. Executive Summary

This document provides a comprehensive audit of the RAGFlow deployment on the NVIDIA DGX server. The system is configured for GPU-accelerated RAG (Retrieval-Augmented Generation) operations with:

- **Deployment Type:** Docker Compose with GPU support
- **Document Engine:** Elasticsearch 8.11.3
- **Primary Use Case:** RAG-based Q&A with automatic document processing
- **Architecture:** Multi-agent system with specialized workflows

Key highlights:
- Full monitoring stack (Prometheus, Grafana, exporters)
- Automated backup system with 7-day retention
- Custom agent workflows for processing, querying, and evaluation
- Hot code deployment capability for rapid iteration

---

## 2. Infrastructure Overview

### 2.1 Docker Services - Core

| Service | Image | Port(s) | Purpose |
|---------|-------|---------|---------|
| ragflow-gpu | infiniflow/ragflow:nightly | 80, 443, 9380-9382 | Main RAGFlow application (GPU) |
| executor | infiniflow/ragflow:nightly | - | Task processing (3 workers, GPU) |
| mysql | mysql:8.0.39 | 5455 | Primary relational database |
| es01 | elasticsearch:8.11.3 | 1200 | Document search/vector storage |
| redis | valkey/valkey:8 | 6379 | Cache and task queue |
| minio | quay.io/minio/minio | 9000, 9001 | Object storage (documents) |
| tei | text-embeddings-inference | 6380 | Embedding service (Qwen3-0.6B) |

### 2.2 Docker Services - Monitoring

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| prometheus | prom/prometheus:latest | 9090 | Metrics collection |
| grafana | grafana/grafana:latest | 3001 | Metrics visualization |
| node-exporter | prom/node-exporter:latest | 9100 | Host system metrics |
| cadvisor | gcr.io/cadvisor/cadvisor:latest | 8080 | Container metrics |
| mysql-exporter | prom/mysqld-exporter:latest | 9104 | MySQL metrics |
| elasticsearch-exporter | prometheuscommunity/elasticsearch-exporter:latest | 9114 | Elasticsearch metrics |
| redis-exporter | oliver006/redis_exporter:latest | 9121 | Redis metrics |
| blackbox-exporter | prom/blackbox-exporter:latest | 9115 | HTTP/TCP endpoint probing |

### 2.3 Resource Configuration

```
Memory Limit: 16 GB (17179869184 bytes)
Thread Pool: 128 workers
Doc Bulk Size: 4
Embedding Batch Size: 16
GPU: NVIDIA (all devices)
```

---

## 3. Custom Agents

| Agent | ID | Trigger | Purpose |
|-------|-----|---------|---------|
| Main | `a0aaa8d1fc2611f0ab2ba6f4b3787fc9` | User query | Primary RAG Q&A agent |
| Process | `35756e8bfb6d11f081a03627fe966451` | Document upload | Automatic document processing |
| Judge | `383dade900bd11f1b85ae224cadbab2f` | Weekly cron | RAG quality evaluation |
| CEO | `1d9dc88af08b11f095da3ae7a67b446b` | Manual | Executive assistant agent |
| PROD_AGENT | `bda78a09f13a11f0938e3ae7a67b446b` | Manual | Production agent |

### Agent Architecture

```
Document Upload → Process Agent → Index to KB
                       ↑
                  (KB pipeline_id trigger)

User Query → Main Agent → Response
                  ↓
           Weekly: Judge Agent → Evaluation Report
```

---

## 4. Customizations from Upstream

### 4.1 Modified Source Files

| File | Change Description |
|------|-------------------|
| `agent/templates/main0_agent.json` | Custom RAG agent with concise answer prompts, citation formatting |
| `api/apps/sdk/doc.py` | Auto-parse feature for documents with `pipeline_id` |
| `api/db/services/dialog_service.py` | Custom dialog handling (mounted via volume) |
| `rag/nlp/search.py` | End-of-answer citation formatting (References: [ID:N]) |

### 4.2 Custom Scripts

| Script | Purpose |
|--------|---------|
| `scripts/deploy_code.sh` | Hot code deployment to containers |
| `scripts/backup_ragflow.sh` | Automated backup (MySQL, MinIO, ES) |
| `scripts/restore_ragflow.sh` | Backup restoration |
| `scripts/run_judge_weekly.sh` | Weekly RAG evaluation cron |
| `scripts/setup_document_trigger.sh` | KB pipeline linking for auto-processing |

### 4.3 Infrastructure Additions

| File/Directory | Purpose |
|----------------|---------|
| `docker/docker-compose-production.yml` | Production overlay with monitoring |
| `docker/monitoring/prometheus.yml` | Prometheus scrape configuration |
| `docker/monitoring/blackbox.yml` | Endpoint probing configuration |
| `docker/monitoring/grafana/` | Grafana provisioning (dashboards, datasources) |
| `PRODUCTION_SETUP.md` | Deployment and operations documentation |

---

## 5. Configuration Summary

### 5.1 Environment Configuration (`.env`)

```env
# Core Settings
COMPOSE_PROFILES=gpu,elasticsearch
DOC_ENGINE=elasticsearch
DEVICE=gpu

# Elasticsearch
STACK_VERSION=8.11.3
ES_HOST=es01
ES_PORT=1200

# MySQL
MYSQL_HOST=mysql
MYSQL_PORT=5455
MYSQL_DBNAME=rag_flow

# MinIO
MINIO_HOST=minio
MINIO_PORT=9000
MINIO_CONSOLE_PORT=9001

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# HTTP Ports
SVR_WEB_HTTP_PORT=80
SVR_WEB_HTTPS_PORT=443
SVR_HTTP_PORT=9380
ADMIN_SVR_HTTP_PORT=9381
SVR_MCP_PORT=9382

# Embedding Service
TEI_MODEL=Qwen/Qwen3-Embedding-0.6B
TEI_HOST=tei
TEI_PORT=6380

# Misc
TIMEZONE=Asia/Shanghai
REGISTER_ENABLED=1
```

### 5.2 Volume Mounts (Development)

```yaml
# Mounted for local development/hot fixes
- ../rag/prompts:/ragflow/rag/prompts:ro
- ../api/db/services/dialog_service.py:/ragflow/api/db/services/dialog_service.py:ro
```

---

## 6. Network Architecture

```
                    ┌─────────────────────────────────────┐
                    │           External Access           │
                    └─────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                    ▼               ▼               ▼
               HTTP:80        HTTPS:443        API:9380
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │     Nginx (ragflow-gpu)       │
                    │   - Static files (frontend)   │
                    │   - SSL termination           │
                    │   - Reverse proxy             │
                    └───────────────┬───────────────┘
                                    │
              ┌────────────┬────────┴────────┬────────────┐
              │            │                 │            │
              ▼            ▼                 ▼            ▼
         MySQL:5455   ES:1200           Redis:6379   MinIO:9000
              │            │                 │            │
              └────────────┴────────┬────────┴────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │      Internal Network         │
                    │      (docker_ragflow)         │
                    │                               │
                    │   executor (task workers)     │
                    │   tei (embeddings:6380)       │
                    │   monitoring stack            │
                    └───────────────────────────────┘

Admin API: 9381 (internal admin operations)
MCP Server: 9382 (Model Context Protocol)
```

### Port Summary

| Port | Service | Access |
|------|---------|--------|
| 80 | Web UI (HTTP) | External |
| 443 | Web UI (HTTPS) | External |
| 9380 | RAGFlow API | External |
| 9381 | Admin API | Internal |
| 9382 | MCP Server | Internal |
| 5455 | MySQL | Internal |
| 1200 | Elasticsearch | Internal |
| 6379 | Redis | Internal |
| 9000/9001 | MinIO | Internal |
| 6380 | TEI Embeddings | Internal |
| 9090 | Prometheus | Internal |
| 3001 | Grafana | Internal |

---

## 7. Backup & Recovery

### 7.1 Backup Strategy

| Component | Method | Location |
|-----------|--------|----------|
| MySQL | `mysqldump` with routines/triggers | `mysql_rag_flow.sql.gz` |
| MinIO | Docker volume copy | `minio_data.tar.gz` |
| Elasticsearch | Snapshot API | `elasticsearch/` directory |
| Configuration | File copy | `config/` directory |

### 7.2 Retention Policy

- **Default Retention:** 7 days
- **Location:** `/ragflow/backups/`
- **Naming:** `ragflow_backup_YYYYMMDD_HHMMSS/`
- **Manifest:** JSON metadata per backup

### 7.3 Backup Commands

```bash
# Manual backup
MYSQL_PASSWORD="..." ./scripts/backup_ragflow.sh /path/to/backups

# Restore
./scripts/restore_ragflow.sh /path/to/backup/ragflow_backup_20260203_083254
```

### 7.4 Cron Schedule (Recommended)

```cron
# Daily backup at 2 AM
0 2 * * * MYSQL_PASSWORD="..." /home/edgelab/ragflow/scripts/backup_ragflow.sh

# Weekly Judge evaluation (Sunday midnight)
0 0 * * 0 RAGFLOW_API_KEY="..." /home/edgelab/ragflow/scripts/run_judge_weekly.sh
```

---

## 8. Recent Changes (Git Log)

| Commit | Description |
|--------|-------------|
| `4d547a1c` | Fix inline citations to appear at end of answers instead of inline |
| `33018cc2` | Add volume mounts for local code development in Docker |
| `067d39d5` | Fix RAG retrieval being skipped when knowledge parameter not configured |
| `5ed0bdcc` | Add Prometheus exporters for full monitoring coverage |
| `5229f8b2` | Add auto-parse for KBs with pipeline_id set |
| `5c05623d` | Fix document trigger setup script to use database |
| `1a337940` | Separate Process and Main agent triggers |
| `08050551` | Fix backup script container names and MinIO backup method |
| `6202288e` | Fix Grafana port conflict and add network config |
| `3752e9e5` | Add production monitoring and backup infrastructure |

---

## 9. Health Check Endpoints

| Endpoint | Purpose | Expected Response |
|----------|---------|-------------------|
| `GET /api/v1/system/version` | API health | JSON with version info |
| `GET /api/v1/system/ping` | API ping | 200 OK |
| `GET :9000/minio/health/live` | MinIO health | 200 OK |
| MySQL: `mysqladmin ping` | Database health | `mysqld is alive` |
| Redis: `redis-cli ping` | Cache health | `PONG` |
| ES: `GET :1200/_cluster/health` | Search health | JSON with cluster status |
| Prometheus: `GET :9090/-/healthy` | Metrics health | 200 OK |
| Grafana: `GET :3001/api/health` | Dashboard health | JSON with status |

### Health Check Commands

```bash
# RAGFlow API
curl -s http://localhost:9380/api/v1/system/version

# All containers
docker ps --format "table {{.Names}}\t{{.Status}}"

# Elasticsearch cluster
curl -s http://localhost:1200/_cluster/health?pretty

# Redis
docker exec docker-redis-1 redis-cli ping
```

---

## 10. Security Considerations

### 10.1 Access Control

- **Authentication:** API key-based authentication for all endpoints
- **HTTPS:** SSL certificates configured in `docker/nginx/ssl/`
- **CORS:** Configurable via `CORS_ORIGIN` environment variable
- **Registration:** Controlled via `REGISTER_ENABLED` flag

### 10.2 Service Isolation

- **Network:** Isolated Docker network (`docker_ragflow`)
- **Sandbox:** Optional sandbox executor for code execution (seccomp enabled)
- **No Privilege Escalation:** `no-new-privileges:true` for sandbox

### 10.3 Credential Management

All sensitive credentials are stored in `docker/.env`:
- MySQL password
- Elasticsearch password
- MinIO credentials
- Redis password
- RAGFlow secret key

**Recommendation:** Rotate credentials periodically and use secrets management in production.

### 10.4 Data Protection

- **Logging:** JSON file driver with rotation (100MB, 5 files)
- **Backups:** Encrypted at rest (recommended)
- **Volume Persistence:** Named Docker volumes for all data stores

---

## 11. Monitoring & Alerting

### 11.1 Grafana Dashboards

Access: `http://localhost:3001`
Credentials: `admin` / `ragflow123`

Pre-configured dashboards:
- RAGFlow Overview (custom)
- Node Exporter (system metrics)
- Container metrics (cAdvisor)

### 11.2 Prometheus Targets

| Target | Endpoint | Metrics |
|--------|----------|---------|
| Node Exporter | :9100/metrics | CPU, memory, disk, network |
| cAdvisor | :8080/metrics | Container resource usage |
| MySQL Exporter | :9104/metrics | Query performance, connections |
| ES Exporter | :9114/metrics | Index size, search latency |
| Redis Exporter | :9121/metrics | Memory, connections, ops/sec |
| Blackbox | :9115/probe | Endpoint availability |

### 11.3 Log Locations

| Log | Location |
|-----|----------|
| RAGFlow server | `docker/ragflow-logs/` |
| Backup logs | `/home/edgelab/ragflow/logs/backup.log` |
| Judge logs | `/home/edgelab/ragflow/logs/judge.log` |
| Container logs | `docker logs <container-name>` |

---

## 12. Operational Procedures

### 12.1 Starting the Stack

```bash
cd /home/edgelab/ragflow/docker

# Full production stack with monitoring
docker compose -f docker-compose.yml -f docker-compose-production.yml --profile gpu up -d
```

### 12.2 Stopping the Stack

```bash
cd /home/edgelab/ragflow/docker
docker compose -f docker-compose.yml -f docker-compose-production.yml --profile gpu down
```

### 12.3 Hot Code Deployment

```bash
# Deploy code changes without rebuilding image
./scripts/deploy_code.sh

# Deploy without restart (for batch updates)
./scripts/deploy_code.sh --no-restart
```

### 12.4 Viewing Logs

```bash
# RAGFlow server (filter out heartbeat noise)
docker logs -f docker-ragflow-gpu-1 2>&1 | grep -v heartbeat

# Executor
docker logs -f docker-executor-1

# All services
docker compose logs -f
```

### 12.5 Common Troubleshooting

| Issue | Solution |
|-------|----------|
| Process agent not triggering | Verify KB has `pipeline_id` set via API |
| Empty RAG results | Lower `similarity_threshold`, verify KB has documents |
| Slow responses | Check ES health, increase `top_k` |
| Container restart loop | Check logs, verify dependencies healthy |
| GPU not detected | Verify NVIDIA driver, check `nvidia-smi` |

---

## 13. API Reference (Quick)

### Query Main Agent

```bash
curl -X POST "http://localhost:9380/api/v1/agents/a0aaa8d1fc2611f0ab2ba6f4b3787fc9/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "Your query here", "stream": false}'
```

### Check KB Pipeline Trigger

```bash
curl -s "http://localhost:9380/api/v1/datasets/<kb-id>" \
  -H "Authorization: Bearer YOUR_API_KEY" | jq '.data.pipeline_id'
```

### View Redis Task Queue

```bash
docker exec docker-redis-1 redis-cli -a "$REDIS_PASSWORD" LLEN rag_flow_task_queue
```

---

## 14. Pending Items

- [ ] **Pending:** `scripts/deploy_code.sh` - Not yet committed to git
- [ ] **Consideration:** Implement alerting rules in Prometheus
- [ ] **Consideration:** Add automated testing for agent workflows
- [ ] **Consideration:** Document API key rotation procedure

---

## 15. File Manifest

### Custom Files (Not in Upstream)

```
/home/edgelab/ragflow/
├── PRODUCTION_SETUP.md
├── SYSTEM_AUDIT.md (this file)
├── scripts/
│   ├── deploy_code.sh
│   ├── backup_ragflow.sh
│   ├── restore_ragflow.sh
│   ├── run_judge_weekly.sh
│   └── setup_document_trigger.sh
└── docker/
    ├── docker-compose-production.yml
    └── monitoring/
        ├── prometheus.yml
        ├── blackbox.yml
        └── grafana/
            └── provisioning/
                ├── dashboards/
                │   ├── dashboards.yml
                │   └── ragflow-dashboard.json
                └── datasources/
                    └── datasources.yml
```

### Modified Files (From Upstream)

```
/home/edgelab/ragflow/
├── docker/.env (customized)
├── agent/templates/main0_agent.json (customized)
├── api/apps/sdk/doc.py (auto-parse feature)
└── rag/nlp/search.py (citation formatting)
```

---

*End of System Audit Document*
