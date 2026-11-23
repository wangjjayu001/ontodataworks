import hashlib
import hmac
import os
import secrets
from datetime import timedelta
from fastapi import Request, HTTPException, Depends
from sqlalchemy import select
from .db import SessionLocal, User, LoginSession, Workspace, Membership, now


def hash_password(password):
    salt = secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000)
    return f"pbkdf2-sha256$310000${salt}${value.hex()}"


def verify_password(password, encoded):
    try:
        _, rounds, salt, target = encoded.split("$")
        value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(value, target)
    except (ValueError, TypeError):
        return False


def db_session():
    with SessionLocal() as db:
        yield db


def identity(request: Request, db=Depends(db_session)):
    token = request.cookies.get("wb_session", "")
    session = db.get(LoginSession, hashlib.sha256(token.encode()).hexdigest()) if token else None
    if not session or session.expires_at < now():
        raise HTTPException(401, "请先登录工作空间")
    user = db.get(User, session.user_id)
    if not user or not user.enabled:
        raise HTTPException(401, "账号不可用")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session.csrf):
            raise HTTPException(403, "请求校验失败，请刷新后重试")
        origin = request.headers.get("origin")
        if origin and origin not in os.environ.get("ALLOWED_ORIGINS", "http://127.0.0.1:19090").split(","):
            raise HTTPException(403, "来源不允许")
    request.state.session = session
    return user


def workspace_access(db, user, wid, write=False):
    ws = db.get(Workspace, wid)
    if not ws:
        raise HTTPException(404, "工作空间不存在")
    if user.role != "admin" and not db.scalar(select(Membership.id).where(Membership.user_id == user.id, Membership.workspace_id == wid)):
        raise HTTPException(403, "没有此工作空间的权限")
    if write and user.role not in {"admin", "developer"}:
        raise HTTPException(403, "只读角色不能修改资源")
    return ws
