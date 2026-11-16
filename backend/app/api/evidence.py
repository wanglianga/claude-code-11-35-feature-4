from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import BatchLink, SourceEvent, User
from ..schemas import EvidenceIn
from ..security import get_current_user, require_analyst
from ..services import evidence as evidence_svc

router = APIRouter(prefix="/api/evidence", tags=["evidence"])


@router.get("")
def list_evidence(
    source_type: str | None = None,
    batch_id: str | None = None,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(SourceEvent)
    if source_type:
        q = q.filter_by(source_type=source_type)
    if batch_id:
        event_ids = [l.event_id for l in db.query(BatchLink).filter_by(batch_id=batch_id).all()]
        q = q.filter(SourceEvent.id.in_(event_ids or [-1]))
    events = q.order_by(SourceEvent.event_time.desc()).limit(500).all()
    return [evidence_svc.to_dict(e) for e in events]


@router.post("", status_code=201)
def create_evidence(
    body: EvidenceIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    event = evidence_svc.ingest(db, **body.model_dump())
    return evidence_svc.to_dict(event)


@router.get("/{event_id}")
def get_evidence(
    event_id: int,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    event = db.get(SourceEvent, event_id)
    if event is None:
        raise HTTPException(404, "证据不存在")
    return evidence_svc.to_dict(event)
