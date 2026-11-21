import os
import hashlib
import hmac
import base64
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import User
from .database import get_db

security = HTTPBearer(auto_error=False)
def secret():
    s = os.getenv('JWT_SECRET')
    if os.getenv('ENVIRONMENT') == 'production' and not s:
        raise RuntimeError('JWT_SECRET must be set in production')
    return s or 'docintel-dev-only-replace-this-secret'

def encode_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 420_000)
    return base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()

def check_password(password, encoded):
    try:
        salt, digest = encoded.split('$')
        return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', password.encode(), base64.b64decode(salt), 420_000), base64.b64decode(digest))
    except (ValueError, TypeError):
        return False

def issue_token(user):
    return jwt.encode({'sub': str(user.id), 'exp': datetime.now(timezone.utc) + timedelta(hours=8)}, secret(), algorithm='HS256')

def current_user(creds: HTTPAuthorizationCredentials | None = Depends(security), db: Session = Depends(get_db)):
    if not creds: raise HTTPException(401, 'Missing token')
    try:
        token = jwt.decode(creds.credentials, secret(), algorithms=['HS256'])
        user_id = int(token['sub'])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, 'Invalid or expired token')
    user = db.get(User, user_id)
    if not user: raise HTTPException(401, 'Unknown identity')
    return user
