# AgentRAG - Quick Start Instructions

## Prerequisites
- Docker Desktop installed and running
- OpenAI API key (or Anthropic/Cohere)

## Initial Setup

1. **Create `.env` file** (copy from `.env.example`):
   ```bash
   cp .env.example .env
   ```

2. **Configure Required Variables**:
   - `OPENAI_API_KEY` - Your OpenAI API key (required)
   - `DEFAULT_LLM_MODEL` - Use `gpt-4o-mini` for cost-effective operation
   - `JWT_SECRET` - Generate a secure random string

3. **Start the Application**:
   ```bash
   docker-compose up -d
   ```

4. **Verify Services**:
   ```bash
   docker-compose ps
   ```

## Access Points
- **Frontend**: http://localhost:3001
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **MinIO Console**: http://localhost:9001 (minioadmin/minioadmin)

## Common Commands

### Start/Stop
```bash
# Start all services
docker-compose up -d

# Stop all services
docker-compose down

# Restart specific service
docker-compose restart backend
docker-compose restart celery-worker
```

### Logs
```bash
# View logs
docker-compose logs -f backend
docker-compose logs -f celery-worker

# Last N lines
docker-compose logs --tail 50 backend
```

### Rebuild After Code Changes
```bash
# Rebuild specific service
docker-compose up -d --build backend
docker-compose up -d --build celery-worker

# Rebuild all
docker-compose up -d --build
```

### Force Recreate (for .env changes)
```bash
# When environment variables change, restart is not enough
docker-compose up -d --force-recreate backend celery-worker
```

## Troubleshooting

### Document Processing Issues

**Problem**: Documents stuck in "pending" status
- **Cause**: Celery worker not listening to correct queues or has errors
- **Solution**: Check celery-worker logs and rebuild if needed
  ```bash
  docker-compose logs celery-worker --tail 100
  docker-compose up -d --build celery-worker
  ```

**Problem**: "ModuleNotFoundError: No module named 'pgvector'"
- **Cause**: Container needs rebuild
- **Solution**: 
  ```bash
  docker-compose up -d --build celery-worker
  ```

**Problem**: "'str' object has no attribute 'value'"
- **Cause**: Enum handling issue in tasks.py
- **Fixed**: Use enum directly without `.value` for string enums

### API Key Issues

**Problem**: OpenAI authentication errors (401)
- **Check**: API key is valid and has credits
- **Update**: Edit `.env` file and recreate containers:
  ```bash
  # Edit .env file with new key
  docker-compose up -d --force-recreate backend celery-worker
  ```

### Database/Migration Issues

**Problem**: Database schema errors
- **Solution**: Run migrations manually:
  ```bash
  docker exec -it agentic-rag-system-backend-1 alembic upgrade head
  ```

## Important Notes

1. **Code Changes**: Always rebuild containers after code changes in `backend/` directory
   ```bash
   docker-compose up -d --build backend celery-worker
   ```

2. **Environment Variables**: Use `--force-recreate` when .env changes, not just `restart`

3. **Celery Queues**: Worker must listen to all queues: `celery`, `processing`, `rag`
   - See docker-compose.yml celery-worker command

4. **Cost Optimization**: Use `gpt-4o-mini` instead of `gpt-4o` (15-17x cheaper)

5. **Failed Documents**: Delete and re-upload, they won't auto-retry

## Service Dependencies

```
Backend depends on:
  - PostgreSQL (pgvector)
  - Redis
  - MinIO

Celery Worker depends on:
  - PostgreSQL
  - Redis
  - Backend code (shares same build)

Frontend depends on:
  - Backend API
```

## Health Checks
```bash
# Check if all containers are healthy
docker-compose ps

# Test backend API
curl http://localhost:8000/api/v1/health

# Check database connection
docker exec -it agentic-rag-system-postgres-1 psql -U agentrag -d agentrag -c "SELECT 1;"
```

## Development Workflow

1. Make code changes in `backend/` or `frontend/`
2. Rebuild affected services:
   ```bash
   docker-compose up -d --build <service-name>
   ```
3. Check logs for errors:
   ```bash
   docker-compose logs -f <service-name>
   ```
4. Test the changes through frontend or API docs

## Quick Debug Checklist

- [ ] Docker Desktop is running
- [ ] All containers are "Healthy" in `docker-compose ps`
- [ ] `.env` file exists with valid API keys
- [ ] Backend logs show "Application startup complete"
- [ ] Celery worker shows all 3 queues in startup
- [ ] No errors in `docker-compose logs backend celery-worker`
