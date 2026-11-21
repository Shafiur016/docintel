import io
from sqlalchemy.orm import Session
from sqlalchemy import text
from .models import Document, Chunk
from .retrieval import embed, persist_vector

CHUNK_SIZE = 700
CHUNK_OVERLAP = 120

def extract_content(data: bytes, filename: str) -> str:
    ext = filename.rsplit('.', 1)[-1].lower()
    if ext in ('txt', 'md'):
        return data.decode('utf-8-sig')
    if ext == 'pdf':
        from pypdf import PdfReader
        return '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(data)).pages)
    if ext == 'docx':
        from docx import Document as WordDoc
        doc = WordDoc(io.BytesIO(data))
        return '\n'.join(p.text for p in doc.paragraphs)
    raise ValueError('Unsupported document format')

def chunk_text(content: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if overlap >= chunk_size: raise ValueError('Overlap must be smaller than chunk size')
    content = ' '.join(content.split())
    if not content: return []
    # Prefer word boundaries, preserving overlap for cross-chunk context.
    results = []
    start = 0
    while start < len(content):
        end = min(start + chunk_size, len(content))
        if end < len(content):
            boundary = content.rfind(' ', start + chunk_size // 2, end)
            if boundary > start: end = boundary
        results.append(content[start:end].strip())
        if end == len(content): break
        start = max(start + 1, end - overlap)
    return results

def process_document(doc_id: int, bind):
    with Session(bind=bind) as db:
        doc = db.get(Document, doc_id)
        if not doc or doc.status != 'queued': return
        doc.status = 'processing'
        db.commit()
        try:
            content = extract_content(doc.raw_data, doc.filename)
            parts = chunk_text(content)
            if not parts: raise ValueError('No readable text; scanned PDFs need OCR before upload')
            vectors = embed(parts)
            for i, (part, vector) in enumerate(zip(parts, vectors)):
                chunk = Chunk(doc_id=doc.id, user_id=doc.user_id, ordinal=i + 1, content=part, embedding=vector)
                db.add(chunk); db.flush()
                persist_vector(db, chunk)
            doc.raw_data = None
            doc.status = 'ready'
            db.commit()
        except Exception as ex:
            db.rollback()
            doc = db.get(Document, doc_id)
            doc.status = 'failed'; doc.error = str(ex)[:200]; doc.raw_data = None
            db.commit()
