# DKS

DKS is an AI-assisted document-to-knowledge-base system. It ingests source
documents such as DOCX and PDF files, extracts structured fragments, proposes
article candidates, routes them through a human review gate, and builds a
linked, citation-grounded knowledge base that can later be explored through
articles, graph views, and strict RAG-style Q&A.

## Status

This repository contains an active work-in-progress version of DKS.

Current capabilities:
- document upload and ingestion for DOCX and PDF sources
- fragment extraction, embeddings, and candidate proposal generation
- review-before-build workflow for article creation
- article reading UI with provenance-aware source context
- structural blocks and graph exploration
- grounded Q&A over the project knowledge base

Current limitations:
- PDF extraction is still being actively calibrated
- graph and structural-block workflows are usable but not final
- some agent-generated metadata depends on external LLM quotas

## Architecture

### Backend
- FastAPI
- SQLAlchemy async + PostgreSQL + pgvector
- Redis + arq worker
- PyMuPDF and python-docx for document extraction
- LangChain / LangGraph / Groq for structured agent workflows

### Frontend
- Next.js App Router
- TanStack React Query
- Tailwind CSS
- Framer Motion

## Repository layout

```text
app/         Backend API, models, domain logic, workers, agents
dks-ui/      Next.js frontend
tests/       Integration and pipeline tests
alembic/     Database migration setup
```

## Local development

### 1. Create environment files

Copy the example files:

```bash
cp .env.example .env
cp dks-ui/.env.example dks-ui/.env.local
```

### 2. Start services

```bash
docker compose up --build
```

This starts:
- PostgreSQL with pgvector
- Redis
- FastAPI backend
- arq worker
- Next.js frontend

### 3. Open the app

- Frontend: [http://localhost:3000](http://localhost:3000)
- Backend API: [http://localhost:8000](http://localhost:8000)
- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

## Running tests

If you already have the services running:

```bash
docker compose up -d db redis
docker compose exec db createdb -U dks dks_test || true
docker compose exec db psql -U dks -d dks_test -c "CREATE EXTENSION IF NOT EXISTS vector;"

TEST_DATABASE_URL=postgresql+asyncpg://dks:dks_secret@localhost:5432/dks_test \
python -m pytest -q
```

## Environment variables

See `.env.example` and `dks-ui/.env.example` for the expected shape.

Important variables:
- `DATABASE_URL`
- `REDIS_URL`
- `OPENAI_API_KEY`
- `GROQ_API_KEY`
- `UPLOAD_DIR`
- `CORS_ALLOWED_ORIGINS`
- `NEXT_PUBLIC_API_URL`

## License

This repository is **not open source**.

It is provided for viewing and evaluation purposes only. See the
[LICENSE](LICENSE) file for full terms.
