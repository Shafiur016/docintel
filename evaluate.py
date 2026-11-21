"""Run retrieval recall@k on user-annotated JSONL rows:
{"question": "...", "expected_filename": "some-document.txt"}
"""
import argparse
import json
from sqlalchemy import select
from app.database import SessionLocal
from app.models import User
from app.retrieval import retrieve

parser = argparse.ArgumentParser()
parser.add_argument('--file', required=True)
parser.add_argument('--email', required=True)
parser.add_argument('--k', type=int, default=5)
args = parser.parse_args()
with SessionLocal() as db:
    user = db.scalar(select(User).where(User.email == args.email))
    if user is None: raise SystemExit('User not found')
    rows = [json.loads(line) for line in open(args.file, encoding='utf-8') if line.strip()]
    if not rows: raise SystemExit('Evaluation file is empty')
    hits = sum(any(c['filename'] == row['expected_filename'] for c in retrieve(db, user.id, row['question'], args.k)) for row in rows)
    print(json.dumps({'queries': len(rows), f'recall@{args.k}': round(hits / len(rows), 4)}, indent=2))
