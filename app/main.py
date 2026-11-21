from contextlib import asynccontextmanager
from time import monotonic
import os
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .database import Base, engine, get_db
from .models import User, Document, Chunk, Usage
from .security import current_user, encode_password, check_password, issue_token
from .retrieval import retrieve, setup_vector_index
from .ingest import process_document
from .generation import answer

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    setup_vector_index(engine)
    yield

app = FastAPI(title='DocIntel | Grounded Document RAG', lifespan=lifespan)
ALLOWED = {'txt', 'md', 'pdf', 'docx'}
MAX_BYTES = 5 * 1024 * 1024

class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)

class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)

@app.get('/healthz')
def health(): return {'status': 'ok'}

@app.post('/auth/register', status_code=201)
def register(body: Credentials, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == str(body.email).lower())):
        raise HTTPException(409, 'Email already registered')
    user = User(email=str(body.email).lower(), password_hash=encode_password(body.password))
    db.add(user); db.commit(); db.refresh(user)
    return {'access_token': issue_token(user), 'token_type': 'bearer'}

@app.post('/auth/login')
def login(body: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == str(body.email).lower()))
    if not user or not check_password(body.password, user.password_hash):
        raise HTTPException(401, 'Invalid credentials')
    return {'access_token': issue_token(user), 'token_type': 'bearer'}

def view(doc):
    return {'id': doc.id, 'filename': doc.filename, 'status': doc.status, 'error': doc.error,
            'created_at': doc.created_at.isoformat()}

@app.post('/documents', status_code=202)
async def upload(bg: BackgroundTasks, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    filename = file.filename or ''
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if extension not in ALLOWED: raise HTTPException(415, 'Supported types: .txt, .md, .pdf, .docx')
    raw = await file.read(MAX_BYTES + 1)
    if not raw: raise HTTPException(400, 'Empty file')
    if len(raw) > MAX_BYTES: raise HTTPException(413, 'Maximum upload size is 5 MB')
    doc = Document(user_id=user.id, filename=filename.rsplit('/', 1)[-1].rsplit('\\', 1)[-1][:255], content_type=file.content_type or '', raw_data=raw)
    db.add(doc); db.commit(); db.refresh(doc)
    if os.getenv('PROCESS_MODE') == 'celery':
        from .worker import celery_app
        if celery_app is None: raise HTTPException(503, 'Celery dependency missing')
        celery_app.send_task('docintel.process', args=[doc.id])
    else:
        bg.add_task(process_document, doc.id, db.bind)
    return view(doc)

@app.get('/documents')
def list_documents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [view(d) for d in db.scalars(select(Document).where(Document.user_id == user.id).order_by(Document.id.desc())).all()]

@app.get('/documents/{doc_id}')
def get_document(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = db.scalar(select(Document).where(Document.id == doc_id, Document.user_id == user.id))
    if not d: raise HTTPException(404, 'Document not found')
    return view(d)

@app.delete('/documents/{doc_id}', status_code=204)
def delete_document(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = db.scalar(select(Document).where(Document.id == doc_id, Document.user_id == user.id))
    if not doc: raise HTTPException(404, 'Document not found')
    if db.bind.dialect.name == 'postgresql':
        db.execute(text('DELETE FROM chunk_vectors WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id = :id)'), {'id': doc.id})
    db.query(Chunk).filter(Chunk.doc_id == doc.id, Chunk.user_id == user.id).delete()
    db.delete(doc); db.commit()

@app.post('/ask')
def ask(body: Ask, user: User = Depends(current_user), db: Session = Depends(get_db)):
    started = monotonic()
    sources = retrieve(db, user.id, body.question, body.top_k)
    response, metering = answer(body.question, sources)
    elapsed = int((monotonic() - started) * 1000)
    # Model rates are configured, not hardcoded (real provider prices vary).
    cost = ((metering['prompt_tokens'] * float(os.getenv('INPUT_USD_PER_1M', '0'))
            + metering['completion_tokens'] * float(os.getenv('OUTPUT_USD_PER_1M', '0'))) / 1e6)
    db.add(Usage(user_id=user.id, latency_ms=elapsed, prompt_tokens=metering['prompt_tokens'],
                 completion_tokens=metering['completion_tokens'], estimated_cost_usd=f'{cost:.8f}', mode=metering['mode']))
    db.commit()
    citations = [{'ref': f'[{i}]', 'document_id': s['doc_id'], 'filename': s['filename'], 'chunk': s['ordinal'], 'score': round(s['score'], 4),
                  'excerpt': s['content'][:400]} for i, s in enumerate(sources, 1)]
    return {'answer': response, 'citations': citations, 'mode': metering['mode'], 'latency_ms': elapsed,
            'usage': {'prompt_tokens': metering['prompt_tokens'], 'completion_tokens': metering['completion_tokens'], 'estimated_cost_usd': cost}}

@app.get('/usage')
def usage(user: User = Depends(current_user), db: Session = Depends(get_db)):
    logs = db.scalars(select(Usage).where(Usage.user_id == user.id).order_by(Usage.id.desc()).limit(100)).all()
    return [{'latency_ms': l.latency_ms, 'prompt_tokens': l.prompt_tokens, 'completion_tokens': l.completion_tokens,
             'estimated_cost_usd': l.estimated_cost_usd, 'mode': l.mode} for l in logs]
