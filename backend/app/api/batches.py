from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import (
    AmountFact,
    Batch,
    BatchLink,
    Contract,
    DepartureCheck,
    MeasurementFact,
    PositionSegment,
    Publication,
    SourceEvent,
    User,
)
from ..schemas import BatchIn, DepartureCheckIn, LinkDecisionIn, LinkIn
from ..security import get_current_user, require_analyst
from ..services import adjudicator, compiler, departure, evidence as evidence_svc, publisher, registry

router = APIRouter(prefix="/api", tags=["batches"])


def _get_batch(db: OrmSession, batch_id: str) -> Batch:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "批次不存在")
    return batch


def _batch_summary(db: OrmSession, b: Batch) -> dict:
    fact = (
        db.query(MeasurementFact)
        .filter_by(batch_id=b.id)
        .order_by(MeasurementFact.version.desc())
        .first()
    )
    amount = (
        db.query(AmountFact)
        .filter_by(batch_id=b.id, status="issued")
        .order_by(AmountFact.interpretation_version.desc())
        .first()
    )
    pub = (
        db.query(Publication)
        .filter_by(batch_id=b.id)
        .order_by(Publication.version.desc())
        .first()
    )
    return {
        "id": b.id,
        "contract_id": b.contract_id,
        "contract_name": b.contract.name,
        "strategy": b.contract.strategy,
        "vehicle_id": b.vehicle_id,
        "service_time": b.service_time.isoformat(),
        "status": b.status,
        "fact_status": fact.status if fact else None,
        "billing_weight": fact.billing_weight if fact else None,
        "acceptable_low": fact.acceptable_low if fact else None,
        "acceptable_high": fact.acceptable_high if fact else None,
        "amount": amount.amount if amount else None,
        "amount_low": amount.amount_low if amount else None,
        "amount_high": amount.amount_high if amount else None,
        "cross_tier": amount.cross_tier if amount else None,
        "publication": pub.downstream_ref if pub else None,
        "publication_state": pub.state if pub else None,
    }


@router.get("/batches")
def list_batches(
    strategy: str | None = None,
    status: str | None = None,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(Batch).join(Contract)
    if strategy:
        q = q.filter(Contract.strategy == strategy)
    if status:
        q = q.filter(Batch.status == status)
    return [_batch_summary(db, b) for b in q.order_by(Batch.service_time.desc()).all()]


@router.post("/batches", status_code=201)
def create_batch(
    body: BatchIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    if db.get(Batch, body.id):
        raise HTTPException(409, "批次号已存在")
    if db.get(Contract, body.contract_id) is None:
        raise HTTPException(404, "合同不存在")
    batch = Batch(**body.model_dump())
    db.add(batch)
    db.commit()
    return _batch_summary(db, batch)


@router.get("/batches/{batch_id}")
def batch_detail(
    batch_id: str,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """批次全量审计视图：证据链、关联、裁定、金额、发布、定位核查一站回查。"""
    batch = _get_batch(db, batch_id)
    links = db.query(BatchLink).filter_by(batch_id=batch_id).all()
    facts = (
        db.query(MeasurementFact)
        .filter_by(batch_id=batch_id)
        .order_by(MeasurementFact.version.desc())
        .all()
    )
    amounts = (
        db.query(AmountFact)
        .filter_by(batch_id=batch_id)
        .order_by(AmountFact.interpretation_version.desc())
        .all()
    )
    pubs = (
        db.query(Publication)
        .filter_by(batch_id=batch_id)
        .order_by(Publication.version.desc())
        .all()
    )
    checks = (
        db.query(DepartureCheck)
        .filter_by(batch_id=batch_id)
        .order_by(DepartureCheck.id.desc())
        .all()
    )
    c = batch.contract
    return {
        **_batch_summary(db, batch),
        "contract": {
            "id": c.id,
            "name": c.name,
            "contract_type": c.contract_type,
            "strategy": c.strategy,
            "source_priority": c.source_priority,
            "time_tolerance_minutes": c.time_tolerance_minutes,
            "quality_tolerance_pct": c.quality_tolerance_pct,
            "approved_conversion": c.approved_conversion,
        },
        "links": [
            {
                **registry.to_dict(l),
                "event": evidence_svc.to_dict(db.get(SourceEvent, l.event_id)),
            }
            for l in links
        ],
        "measurement_facts": [adjudicator.to_dict(f) for f in facts],
        "amount_facts": [compiler.to_dict(a) for a in amounts],
        "publications": [publisher.to_dict(p) for p in pubs],
        "departure_checks": [departure.to_dict(ch) for ch in checks],
    }


@router.post("/batches/{batch_id}/links", status_code=201)
def add_link(
    batch_id: str,
    body: LinkIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    batch = _get_batch(db, batch_id)
    if db.get(SourceEvent, body.event_id) is None:
        raise HTTPException(404, "证据事件不存在")
    link = registry.add_link(db, batch, body.event_id, body.link_type)
    return registry.to_dict(link)


@router.post("/links/{link_id}/confirm")
def confirm_link(
    link_id: int,
    body: LinkDecisionIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    link = db.get(BatchLink, link_id)
    if link is None:
        raise HTTPException(404, "关联不存在")
    return registry.to_dict(registry.confirm_link(db, link, body.reason))


@router.post("/links/{link_id}/reject")
def reject_link(
    link_id: int,
    body: LinkDecisionIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    link = db.get(BatchLink, link_id)
    if link is None:
        raise HTTPException(404, "关联不存在")
    return registry.to_dict(registry.reject_link(db, link, body.reason))


@router.post("/batches/{batch_id}/adjudicate")
def adjudicate(
    batch_id: str,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    batch = _get_batch(db, batch_id)
    return adjudicator.to_dict(adjudicator.adjudicate(db, batch))


@router.post("/batches/{batch_id}/compile")
def compile_amounts(
    batch_id: str,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    batch = _get_batch(db, batch_id)
    return compiler.to_dict(compiler.compile_amounts(db, batch))


@router.post("/batches/{batch_id}/publish")
def publish(
    batch_id: str,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    batch = _get_batch(db, batch_id)
    return publisher.to_dict(publisher.publish(db, batch))


@router.post("/batches/{batch_id}/departure-check")
def departure_check(
    batch_id: str,
    body: DepartureCheckIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    batch = _get_batch(db, batch_id)
    return departure.to_dict(departure.check_departure(db, batch, body.tolerance_minutes))


@router.get("/vehicles/{vehicle_id}/positions")
def vehicle_positions(
    vehicle_id: str,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    segments = (
        db.query(PositionSegment)
        .filter_by(vehicle_id=vehicle_id)
        .order_by(PositionSegment.end_time)
        .all()
    )
    return [
        {
            "id": s.id,
            "vehicle_id": s.vehicle_id,
            "site_id": s.site_id,
            "start_time": s.start_time.isoformat(),
            "end_time": s.end_time.isoformat(),
            "clock_offset_seconds": s.clock_offset_seconds,
        }
        for s in segments
    ]
