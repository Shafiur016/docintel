# DocIntel — Authenticated Document Intelligence & Grounded Retrieval API

Ingest PDF, DOCX, TXT or Markdown files; chunk documents; retrieve context for a question; return traceable source citations. Includes user-scoped access control, latency/token/cost logs, offline lexical search and **optional** OpenAI-backed semantic embeddings + LLM answers. PostgreSQL mode uses **pgvector** for vector retrieval and Redis/Celery for durable ingestion jobs.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
pytest -q
# API docs at http://localhost:8001/docs
```

Local mode runs SQLite, deterministic lexical vectorization, extractive answers, and FastAPI in-process background tasks. **It does not require an API key**, but it is *not* semantic LLM RAG in offline mode.

## Full Docker stack

```bash
cp .env.example .env
# Set unique database password and JWT_SECRET.
docker compose up --build
```

Docker enables PostgreSQL/pgvector + Redis/Celery. Set `EMBEDDING_PROVIDER=openai`, `ENABLE_LLM=1`, and an `OPENAI_API_KEY` to enable provider embeddings/answers. Provider API requests can incur fees; source documents are sent to the provider. Switching embedding providers after indexing requires deleting/re-ingesting documents: lexical and semantic embedding spaces are **not compatible**.

## API usage

1. `POST /auth/register` (`email`, `password`) to obtain a JWT.
2. Upload `POST /documents` using multipart field `file` with bearer JWT; status initially `queued`.
3. Poll `GET /documents/{id}` until `ready` or `failed`.
4. `POST /ask` with `{ "question": "Which database supports transactions?", "top_k": 5 }`.
5. Examine `citations[]` (filename, document ID, chunk, excerpt, score) and `GET /usage`.
6. `DELETE /documents/{id}` deletes its chunks and pgvector rows.

## Evaluation

Populate `examples/eval_sample.jsonl` with your own query / expected filename pairs, then run:

```bash
python evaluate.py --email your@email.com --file examples/eval_sample.jsonl --k 5
```

Reports retrieval recall@k from your own judgments, never fabricated scores.

## Limits and security

- Each retrieval query, document lookup, delete, and chunk is **owner-scoped**. JWT is signed and expires after 8 hours.
- Upload limit 5 MB, supported extensions only. Scanned PDFs require OCR, which is not included.
- Extractive answers in offline mode can only cite passages actually indexed; optional LLM answers can still hallucinate and must be reviewed.
- No antivirus scanning, rate limiting, OCR pipeline, distributed object storage, PII redaction, or robust audit trail. Extend before production use.
- Postgres pgvector extension requires suitable DB privileges. Approximate vector indexes, high availability, persistent object storage and detailed security hardening are follow-on work.
- `estimated_cost_usd` is **only** a computed estimate from user-configured provider rates; zero by default, not a provider invoice.
