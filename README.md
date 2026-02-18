# 🚀 AgentRAG — Production-Ready Agentic RAG-as-a-Service

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-green.svg)](https://python.org)
[![React 18](https://img.shields.io/badge/React-18-61DAFB.svg)](https://reactjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://docker.com)

> **Enterprise-grade, multi-tenant Agentic RAG platform** with multi-modal ingestion, advanced retrieval strategies, agentic AI orchestration, and a beautiful React UI.

---

## 📋 Table of Contents

- [Features](#-features)
- [Architecture](#-architecture)
- [Feature Comparison](#-feature-comparison)
- [Quick Start](#-quick-start)
- [Configuration](#-configuration)
- [API Reference](#-api-reference)
- [Frontend](#-frontend)
- [Multi-Tenancy & Security](#-multi-tenancy--security)
- [Rate Limiting & Tiers](#-rate-limiting--tiers)
- [RAG Pipeline](#-rag-pipeline)
- [Agentic AI](#-agentic-ai)
- [Deployment](#-deployment)
- [Contributing](#-contributing)

---

## ✨ Features

### Multi-Modal Ingestion
- 📄 **Documents**: PDF, DOCX, TXT, Markdown, CSV, XLSX, HTML
- 🖼️ **Images**: OCR (Tesseract), Vision models (GPT-4V, Claude), EXIF extraction
- 🎵 **Audio**: Whisper transcription, speaker diarization, chapter indexing
- 🎬 **Video**: Frame extraction, audio transcription, scene detection
- 🌐 **Web**: URL crawling, sitemap ingestion, recursive scraping

### Advanced RAG Pipeline
- **Hybrid Search**: Dense (embeddings) + Sparse (BM25) + Keyword fusion
- **Re-ranking**: Cross-encoder re-ranking (Cohere, local models)
- **Query Enhancement**: Multi-query expansion, HyDE (Hypothetical Document Embeddings)
- **Chunking**: Fixed, semantic, recursive, document-structure-aware, parent-child
- **Contextual Compression**: LLM-based context distillation
- **Recursive Retrieval**: Multi-hop reasoning with iterative refinement
- **Knowledge Graphs**: Entity extraction, relationship mapping, graph-enhanced retrieval
- **Citations**: Source attribution with exact passage highlighting

### Agentic AI
- **Agent Orchestration**: Multi-step reasoning with tool use
- **Autonomous Planning**: Query decomposition and retrieval planning
- **Tool Integration**: Calculator, web search, code execution, API calls
- **Chain-of-Thought**: Transparent reasoning with step-by-step traces
- **Multi-Agent**: Specialized agents (researcher, summarizer, analyst) with delegation

### Multi-Tenancy & Security
- **Complete Tenant Isolation**: Row-level security in PostgreSQL
- **User-Level Privacy**: Private collections within tenants
- **Auth**: JWT + OAuth2 (Google, GitHub SSO)
- **RBAC**: Admin, Member, Viewer roles with granular permissions
- **API Keys**: Per-tenant API key management
- **Audit Logging**: Full action audit trail

### Production Ready
- **Streaming**: SSE + WebSocket real-time responses
- **Rate Limiting**: Tier-based (Free/Pro/Enterprise) with Redis
- **Async Processing**: Celery workers for document ingestion
- **Observability**: Structured logging, Prometheus metrics, health checks
- **Database Migrations**: Alembic with zero-downtime migrations
- **Docker**: Full docker-compose stack
- **OpenAPI**: Auto-generated Swagger/ReDoc documentation

---

## 🏗 Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        React Frontend                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │  Auth UI  │ │ Chat UI  │ │ Doc Mgmt │ │ Settings │           │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘           │
│       └─────────────┴────────────┴─────────────┘                 │
└──────────────────────────┬───────────────────────────────────────┘
                           │ HTTPS / WSS
┌──────────────────────────▼───────────────────────────────────────┐
│                     FastAPI Backend                               │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐          │
│  │ Auth &   │ │ RAG      │ │ Agent    │ │ Rate       │          │
│  │ RBAC     │ │ Engine   │ │ Engine   │ │ Limiter    │          │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬───────┘          │
│       │             │            │             │                  │
│  ┌────▼─────────────▼────────────▼─────────────▼───────┐         │
│  │              Service Layer                           │         │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐            │         │
│  │  │ Chunker  │ │ Embedder │ │ Retriever│            │         │
│  │  ├──────────┤ ├──────────┤ ├──────────┤            │         │
│  │  │ Extractor│ │ Ranker   │ │ Generator│            │         │
│  │  └──────────┘ └──────────┘ └──────────┘            │         │
│  └─────────────────────────────────────────────────────┘         │
└──────┬─────────────┬─────────────┬─────────────┬────────────────┘
       │             │             │             │
┌──────▼──┐  ┌──────▼──┐  ┌──────▼──┐  ┌──────▼──┐
│PostgreSQL│  │pgvector │  │  Redis  │  │ MinIO/  │
│ + RLS    │  │Vector DB│  │Cache/RL │  │   S3    │
└──────────┘  └─────────┘  └─────────┘  └─────────┘
       │
┌──────▼──────────────────┐
│   Celery Workers        │
│  ┌──────┐ ┌──────┐     │
│  │Ingest│ │ OCR  │     │
│  │Worker│ │Worker│ ... │
│  └──────┘ └──────┘     │
└─────────────────────────┘
```

---

## 📊 Feature Comparison

| Feature | AgentRAG | Progress RAG | LangChain | LlamaIndex |
|---------|----------|-------------|-----------|------------|
| Multi-modal ingestion | ✅ All types | ✅ Limited | ⚠️ Plugin | ⚠️ Plugin |
| Agentic AI orchestration | ✅ Multi-agent | ✅ Basic | ✅ Chains | ⚠️ Basic |
| Multi-tenancy + RLS | ✅ Native | ✅ | ❌ | ❌ |
| User-level privacy | ✅ | ❌ | ❌ | ❌ |
| Hybrid search (dense+sparse) | ✅ | ✅ | ⚠️ Manual | ⚠️ Manual |
| Knowledge graphs | ✅ | ❌ | ⚠️ Plugin | ✅ |
| Re-ranking | ✅ Multi-model | ✅ | ⚠️ Manual | ✅ |
| HyDE + multi-query | ✅ | ❌ | ⚠️ Manual | ✅ |
| Parent-child chunking | ✅ | ❌ | ❌ | ✅ |
| Built-in eval metrics | ✅ | ❌ | ❌ | ❌ |
| Streaming (SSE+WS) | ✅ | ✅ | ⚠️ Basic | ⚠️ Basic |
| Tiered rate limiting | ✅ | ✅ | ❌ | ❌ |
| OAuth2 SSO | ✅ | ✅ | ❌ | ❌ |
| React UI (mobile-ready) | ✅ | ✅ | ❌ | ❌ |
| Docker one-command deploy | ✅ | ✅ | ❌ | ❌ |
| Audit logging | ✅ | ✅ | ❌ | ❌ |
| API key management | ✅ | ✅ | ❌ | ❌ |
| Conversation memory | ✅ | ✅ | ✅ | ✅ |
| Citations + highlights | ✅ | ⚠️ Basic | ⚠️ Manual | ✅ |

---

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- (Optional) OpenAI / Anthropic API key for LLM features

### 1. Clone & Configure

```bash
git clone https://github.com/yourusername/agentic-rag-system.git
cd agentic-rag-system
cp .env.example .env
# Edit .env with your API keys and settings
```

### 2. Start Everything

```bash
docker-compose up -d
```

This starts: PostgreSQL + pgvector, Redis, MinIO, FastAPI backend, Celery workers, React frontend.

### 3. Access

- **Frontend**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### 4. Create First Tenant

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "secure123", "org_name": "My Org"}'
```

---

## ⚙️ Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL connection |
| `REDIS_URL` | `redis://localhost:6379` | Redis for cache + rate limiting |
| `MINIO_ENDPOINT` | `localhost:9000` | Object storage |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `COHERE_API_KEY` | — | Cohere re-ranking API key |
| `JWT_SECRET` | (generated) | JWT signing secret |
| `GOOGLE_CLIENT_ID` | — | Google OAuth2 |
| `GITHUB_CLIENT_ID` | — | GitHub OAuth2 |
| `DEFAULT_EMBEDDING_MODEL` | `text-embedding-3-small` | Default embeddings |
| `DEFAULT_LLM_MODEL` | `gpt-4o` | Default LLM |
| `MAX_UPLOAD_SIZE_MB` | `100` | Max file upload size |

### Tenant Tier Configuration

```yaml
tiers:
  free:
    requests_per_minute: 10
    documents_per_month: 50
    storage_gb: 1
    max_collections: 5
    concurrent_queries: 2
    models: [gpt-4o-mini]
  pro:
    requests_per_minute: 60
    documents_per_month: 1000
    storage_gb: 50
    max_collections: 50
    concurrent_queries: 10
    models: [gpt-4o, gpt-4o-mini, claude-3-5-sonnet]
  enterprise:
    requests_per_minute: 300
    documents_per_month: unlimited
    storage_gb: 500
    max_collections: unlimited
    concurrent_queries: 50
    models: [all]
```

---

## 📡 API Reference

### Authentication
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/register` | Register new org + admin |
| POST | `/api/v1/auth/login` | Login, get JWT |
| POST | `/api/v1/auth/refresh` | Refresh JWT token |
| GET | `/api/v1/auth/oauth/{provider}` | OAuth2 redirect |
| POST | `/api/v1/auth/api-keys` | Create API key |

### Collections
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/collections` | Create collection |
| GET | `/api/v1/collections` | List collections |
| PATCH | `/api/v1/collections/{id}` | Update collection |
| DELETE | `/api/v1/collections/{id}` | Delete collection |

### Documents
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/documents/upload` | Upload document(s) |
| POST | `/api/v1/documents/url` | Ingest from URL |
| GET | `/api/v1/documents` | List documents |
| GET | `/api/v1/documents/{id}/status` | Processing status |
| DELETE | `/api/v1/documents/{id}` | Delete document |

### Query & Chat
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/query` | Single RAG query |
| POST | `/api/v1/query/stream` | Streaming RAG query (SSE) |
| WS | `/api/v1/ws/chat` | WebSocket chat |
| POST | `/api/v1/chat/conversations` | Create conversation |
| GET | `/api/v1/chat/conversations` | List conversations |

### Agents
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/agents/execute` | Execute agent task |
| POST | `/api/v1/agents/stream` | Streaming agent execution |
| GET | `/api/v1/agents/types` | List available agent types |

### Admin
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/admin/usage` | Tenant usage stats |
| GET | `/api/v1/admin/users` | Manage users |
| PATCH | `/api/v1/admin/tier` | Update tenant tier |
| GET | `/api/v1/admin/audit-log` | Audit trail |

---

## 🎨 Frontend

### Features
- **Responsive Design**: Works beautifully on desktop, tablet, and mobile
- **Dark/Light Mode**: System-aware theme switching
- **Real-time Chat**: WebSocket-powered conversational UI with streaming
- **Document Manager**: Drag-and-drop upload, progress tracking, preview
- **Collection Browser**: Visual collection management with search
- **Settings Dashboard**: Tenant config, user management, API keys
- **Usage Analytics**: Charts showing usage by tier limits

### Tech Stack
- React 18 + TypeScript
- Tailwind CSS + Headless UI
- React Query (TanStack) for data fetching
- Zustand for state management
- React Router v6
- Recharts for analytics

---

## 🔒 Multi-Tenancy & Security

### Data Isolation
Every database query is scoped by `tenant_id` using PostgreSQL Row-Level Security:

```sql
CREATE POLICY tenant_isolation ON documents
  USING (tenant_id = current_setting('app.current_tenant')::uuid);
```

### User-Level Privacy
Collections can be set to `private` (owner-only), `shared` (org-wide), or `public`.

### Role-Based Access Control
| Permission | Admin | Member | Viewer |
|-----------|-------|--------|--------|
| Manage users | ✅ | ❌ | ❌ |
| Create collections | ✅ | ✅ | ❌ |
| Upload documents | ✅ | ✅ | ❌ |
| Query / Chat | ✅ | ✅ | ✅ |
| View analytics | ✅ | ✅ | ❌ |
| Manage API keys | ✅ | ❌ | ❌ |
| Delete org data | ✅ | ❌ | ❌ |

---

## 🔄 RAG Pipeline

### Ingestion Flow
```
Upload → Extract → Chunk → Embed → Index → Store
  │         │         │        │       │       │
  │    PDF/DOCX/   Semantic   OpenAI  pgvector MinIO
  │    OCR/ASR    Recursive   Cohere  BM25
  │    Vision     Parent-Child HuggingFace
  └─ Celery async worker pool
```

### Retrieval Flow
```
Query → Enhance → Search → Rank → Compress → Generate → Stream
  │        │         │       │        │          │         │
  │    Multi-query  Hybrid  Cross-   Context   LLM w/   SSE/WS
  │    HyDE        Dense+  Encoder  Compress  Citations
  │    Decompose   Sparse  Cohere   Filter
  └─ Agent can iterate (recursive retrieval)
```

### Evaluation Metrics
- **Faithfulness**: Is the answer grounded in retrieved context?
- **Relevance**: Are retrieved documents relevant to the query?
- **Context Precision**: How precise is the retrieved context?
- **Answer Completeness**: Does the answer address all aspects?

---

## 🤖 Agentic AI

### Agent Types
1. **Research Agent**: Multi-step information gathering with source triangulation
2. **Analyst Agent**: Data analysis, comparison, trend identification
3. **Summarizer Agent**: Condensing large document sets
4. **Code Agent**: Code understanding, generation, debugging from codebases
5. **Custom Agents**: Define via YAML configuration

### Agent Orchestration
```python
# Agents can use tools, delegate to other agents, and reason step-by-step
agent = AgentExecutor(
    tools=[retrieval_tool, calculator_tool, web_search_tool],
    llm=tenant_llm,
    strategy="react",  # ReAct reasoning
    max_steps=10,
)
```

---

## 🚢 Deployment

### Docker Compose (Recommended)
```bash
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### Kubernetes
Helm charts available in `/deploy/helm/`.

### Environment Checklist
- [ ] Set strong `JWT_SECRET`
- [ ] Configure API keys
- [ ] Set `CORS_ORIGINS` to your domain
- [ ] Enable HTTPS (reverse proxy)
- [ ] Configure backup for PostgreSQL
- [ ] Set up monitoring (Prometheus + Grafana)

---

## 🧪 Testing

```bash
# Backend tests
cd backend && pytest -v --cov=app

# Frontend tests
cd frontend && npm test
```

---

## 📝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

**Built with ❤️ for the AI community.**
