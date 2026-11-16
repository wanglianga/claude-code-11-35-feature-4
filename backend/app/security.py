"""轻量会话认证：SHA256(盐+密码) 校验，令牌存 sessions 表。"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as OrmSession

from .config import settings
from .db import get_db
from .models import Session, User

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def make_password_hash(password: str) -> str:
    salt = secrets.token_hex(8)
    return f"{salt}${hash_password(password, salt)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), digest)


def create_session(db: OrmSession, user: User) -> Session:
    token = secrets.token_hex(24)
    sess = Session(
        token=token,
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_TTL_HOURS),
    )
    db.add(sess)
    db.commit()
    return sess


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: OrmSession = Depends(get_db),
) -> User:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少访问令牌")
    sess = db.get(Session, cred.credentials)
    if sess is None or sess.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "令牌无效或已过期")
    user = db.get(User, sess.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return user


def require_analyst(user: User = Depends(get_current_user)) -> User:
    if user.role != "analyst":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅数据团队（analyst）可执行写操作")
    return user


def require_supervisor(user: User = Depends(get_current_user)) -> User:
    # 主管（supervisor）：合同确认、投诉核实、合并裁定、申辩裁决、月结锁定与调整
    if user.role != "supervisor":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅物业主管（supervisor）可执行该裁定操作")
    return user
