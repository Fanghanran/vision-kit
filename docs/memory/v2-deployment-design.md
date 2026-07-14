---
name: v2-deployment-design
description: SentinelMind 第二版运维部署详细设计
metadata: 
  node_type: memory
  type: project
  originSessionId: a44998c4-778d-42f2-b4c3-58b9c24bcb0c
---

## 第二版部署设计

详细设计书：`docs/V2_DEPLOYMENT_DESIGN.md`

### 三批实施

| 批次 | 内容 | 优先级 | 预估 |
|------|------|--------|------|
| **第一批** | systemd + 备份 + Docker | 高 | 3 天 |
| **第二批** | HTTPS + RAG | 中 | 4 天 |
| **第三批** | PostgreSQL + 端-云 | 低 | 7 天 |

### 第一批详情

- **V2-1 systemd**：`deploy/sentinelmind.service` + `install.sh` + `uninstall.sh`
- **V2-2 数据备份**：`scripts/backup.sh` + `restore.sh` + `cleanup_backups.sh`
- **V2-3 Docker**：`Dockerfile` + `docker-compose.yml` + `.dockerignore`

### 第二批详情

- **V2-4 HTTPS**：`deploy/nginx/sentinelmind.conf` + `scripts/setup-ssl.sh`
- **V2-6 RAG**：`src/sentinelmind/storage/vector_store.py` + `src/sentinelmind/llm/rag.py`

### 第三批详情

- **V2-5 PostgreSQL**：`scripts/migrate_sqlite_to_postgres.py` + `postgres_backend.py`
- **V2-7 端-云**：`RemoteDetector` + `source_type: edge` 配置

**Why:** 生产环境部署就绪
**How to:** 按批次实施，第一批（systemd + 备份 + Docker）优先
