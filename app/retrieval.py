"""Offline fixed-length lexical embeddings plus optional OpenAI semantic embeddings.

Use EMBEDDING_PROVIDER=openai for true semantic embeddings and pgvector ANN-ready storage.
The offline mode is deterministic lexical retrieval, NOT a semantic model.
"""
import os
import math
import re
import numpy as np
from sqlalchemy import select, text
from sklearn.feature_extraction.text import HashingVectorizer
from .models import Chunk, Document

DIM = 384
vectorizer = HashingVectorizer(n_features=DIM, alternate_sign=False, norm='l2', lowercase=True, token_pattern=r'(?u)\b\w+\b')

def embed(texts: list[str]) -> list[list[float]]:
    if os.getenv('EMBEDDING_PROVIDER', 'local') == 'openai':
        import httpx
        if not os.getenv('OPENAI_API_KEY'): raise RuntimeError('OPENAI_API_KEY required for embedding provider')
        response = httpx.post('https://api.openai.com/v1/embeddings', headers={'Authorization': f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={'model': os.getenv('EMBEDDING_MODEL', 'text-embedding-3-small'), 'input': texts, 'dimensions': DIM}, timeout=60)
        response.raise_for_status()
        return [d['embedding'] for d in sorted(response.json()['data'], key=lambda d: d['index'])]
    return vectorizer.transform(texts).toarray().astype(float).tolist()

def cosine(a: list[float], b: list[float]) -> float:
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denominator = np.linalg.norm(x) * np.linalg.norm(y)
    return float(np.dot(x, y) / denominator) if denominator else 0.0

def setup_vector_index(bind):
    if bind.dialect.name != 'postgresql': return
    with bind.begin() as c:
        c.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        c.execute(text('CREATE TABLE IF NOT EXISTS chunk_vectors (chunk_id integer PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE, user_id integer NOT NULL, embedding vector(384) NOT NULL)'))
        c.execute(text('CREATE INDEX IF NOT EXISTS ix_chunk_vectors_user ON chunk_vectors(user_id)'))
        # HNSW is optional: index creation may be unavailable on older pgvector builds.

def persist_vector(db, chunk):
    if db.bind.dialect.name != 'postgresql': return
    db.execute(text('INSERT INTO chunk_vectors(chunk_id,user_id,embedding) VALUES (:id,:uid,CAST(:v AS vector))'),
               {'id': chunk.id, 'uid': chunk.user_id, 'v': str(chunk.embedding)})

def retrieve(db, user_id: int, question: str, k: int = 5) -> list[dict]:
    query_vector = embed([question])[0]
    if db.bind.dialect.name == 'postgresql':
        rows = db.execute(text('''SELECT c.id, c.doc_id, c.ordinal, c.content, d.filename,
             1-(v.embedding <=> CAST(:vec AS vector)) AS score
             FROM chunk_vectors v JOIN chunks c ON c.id = v.chunk_id
             JOIN documents d ON d.id = c.doc_id
             WHERE v.user_id=:uid AND d.user_id=:uid AND d.status='ready'
             ORDER BY v.embedding <=> CAST(:vec AS vector) LIMIT :lim'''),
             {'vec': str(query_vector), 'uid': user_id, 'lim': k}).mappings().all()
        return [dict(row) for row in rows if row['score'] > 0]
    records = db.execute(select(Chunk, Document.filename).join(Document, Chunk.doc_id == Document.id)
             .where(Chunk.user_id == user_id, Document.user_id == user_id, Document.status == 'ready')).all()
    scored = [(cosine(chunk.embedding, query_vector), chunk, filename) for chunk, filename in records]
    scored.sort(key=lambda r: r[0], reverse=True)
    return [{'id': chunk.id, 'doc_id': chunk.doc_id, 'ordinal': chunk.ordinal, 'content': chunk.content,
             'filename': filename, 'score': round(score, 4)}
            for score, chunk, filename in scored[:k] if score > 0]
