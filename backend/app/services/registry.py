"""批次关系册：任务、车辆、称重与签收之间的候选/确认关联；争用时冻结分配。"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Batch, BatchLink

CONTENTION_REASON = "同一称重记录被多个批次候选引用，关系册冻结分配，待证据确定"


def add_link(db: Session, batch: Batch, event_id: int, link_type: str) -> BatchLink:
    link = BatchLink(batch_id=batch.id, event_id=event_id, link_type=link_type, status="candidate")
    db.add(link)
    db.flush()

    if link_type == "weighing":
        # 同一称重记录被其他批次候选/确认引用 → 双方均冻结
        others = (
            db.query(BatchLink)
            .filter(
                BatchLink.event_id == event_id,
                BatchLink.link_type == "weighing",
                BatchLink.batch_id != batch.id,
                BatchLink.status.in_(["candidate", "confirmed", "frozen"]),
            )
            .all()
        )
        if others:
            link.status = "frozen"
            link.reason = CONTENTION_REASON
            for other in others:
                if other.status != "frozen":
                    other.status = "frozen"
                    other.reason = CONTENTION_REASON
    db.commit()
    db.refresh(link)
    return link


def confirm_link(db: Session, link: BatchLink, reason: str = "") -> BatchLink:
    if link.status == "rejected":
        raise HTTPException(409, "已拒绝的关联不能确认")
    # 冻结的关联允许在证据确定后确认；其余争用关联仍保持冻结，需显式拒绝
    link.status = "confirmed"
    link.reason = reason or ("争用解除：证据确定归属" if link.status == "frozen" else link.reason)
    link.decided_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(link)
    return link


def reject_link(db: Session, link: BatchLink, reason: str = "") -> BatchLink:
    if link.status == "confirmed":
        raise HTTPException(409, "已确认的关联不能拒绝")
    link.status = "rejected"
    link.reason = reason or "证据不足，排除关联"
    link.decided_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(link)
    return link


def to_dict(l: BatchLink) -> dict:
    return {
        "id": l.id,
        "batch_id": l.batch_id,
        "event_id": l.event_id,
        "link_type": l.link_type,
        "status": l.status,
        "reason": l.reason,
        "created_at": l.created_at.isoformat(),
        "decided_at": l.decided_at.isoformat() if l.decided_at else None,
    }
