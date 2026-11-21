"""Optional durable Celery processing via Redis broker."""
import os
from .database import engine
from .ingest import process_document
try:
    from celery import Celery
except ImportError:
    Celery = None
celery_app = Celery('docintel', broker=os.getenv('REDIS_URL', 'redis://redis:6379/0')) if Celery else None
if celery_app:
    @celery_app.task(name='docintel.process', autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
    def process(doc_id: int):
        process_document(doc_id, engine)
