from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import Session as Sess, User
from ..schemas import LoginIn
from ..security import create_session, get_current_user, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginIn, db: OrmSession = Depends(get_db)):
    user = db.query(User).filter_by(username=body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    sess = create_session(db, user)
    return {
        "token": sess.token,
        "user": {"username": user.username, "role": user.role, "display_name": user.display_name},
    }


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"username": user.username, "role": user.role, "display_name": user.display_name}


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: OrmSession = Depends(get_db)):
    db.query(Sess).filter_by(user_id=user.id).delete()
    db.commit()
    return {"ok": True}
