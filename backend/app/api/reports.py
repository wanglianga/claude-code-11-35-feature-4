from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import (
    AmountFact,
    Batch,
    BatchLink,
    Contract,
    MeasurementFact,
    Publication,
    User,
)
from ..security import get_current_user
from ..services import publisher, registry

router = APIRouter(prefix="/api", tags=["reports"])

STRATEGY_LABEL = {"priority": "优先级裁定", "interval": "区间裁定"}


@router.get("/publications")
def list_publications(
    batch_id: str | None = None,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(Publication)
    if batch_id:
        q = q.filter_by(batch_id=batch_id)
    pubs = q.order_by(Publication.id.desc()).limit(200).all()
    return [publisher.to_dict(p) for p in pubs]


@router.get("/review-queue")
def review_queue(db: OrmSession = Depends(get_db), _: User = Depends(get_current_user)):
    """复核队列：冻结关联、待核冲突、复审中计量事实。"""
    frozen = db.query(BatchLink).filter_by(status="frozen").all()
    pending = db.query(MeasurementFact).filter_by(status="pending_conflict").all()
    under_review = db.query(MeasurementFact).filter_by(status="under_review").all()

    def latest_only(facts):
        seen = {}
        for f in sorted(facts, key=lambda x: (x.batch_id, -x.version)):
            seen.setdefault(f.batch_id, f)
        return list(seen.values())

    return {
        "frozen_links": [registry.to_dict(l) for l in frozen],
        "pending_facts": [
            {"id": f.id, "batch_id": f.batch_id, "version": f.version, "conflicts": f.conflicts}
            for f in latest_only(pending)
        ],
        "under_review_facts": [
            {"id": f.id, "batch_id": f.batch_id, "version": f.version, "conflicts": f.conflicts}
            for f in latest_only(under_review)
        ],
    }


@router.get("/reports/monthly")
def monthly_report(
    month: str,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """月结报表：按合同策略分列已确认金额与未决范围，并标明裁定策略。"""
    try:
        start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError:
        return {"error": "month 格式应为 YYYY-MM"}
    end = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=timezone.utc)

    batches = (
        db.query(Batch)
        .filter(Batch.service_time >= start, Batch.service_time < end)
        .order_by(Batch.service_time)
        .all()
    )
    rows, confirmed_total, confirmed_count, pending_count = [], 0.0, 0, 0
    for b in batches:
        pub = (
            db.query(Publication)
            .filter_by(batch_id=b.id)
            .order_by(Publication.version.desc())
            .first()
        )
        amount = (
            db.query(AmountFact)
            .filter_by(batch_id=b.id, status="issued")
            .order_by(AmountFact.interpretation_version.desc())
            .first()
        )
        fact = (
            db.query(MeasurementFact)
            .filter_by(batch_id=b.id)
            .order_by(MeasurementFact.version.desc())
            .first()
        )
        confirmed_amount, pending_range = None, None
        if pub and pub.state == "published" and pub.billing_basis:
            confirmed_amount = pub.billing_basis.get("amount")
            if confirmed_amount is None:
                pending_range = pub.pending_ranges
        elif pub and pub.state in ("pending", "paused"):
            pending_range = pub.pending_ranges
        elif amount and amount.amount is None:
            pending_range = {"amount_low": amount.amount_low, "amount_high": amount.amount_high}

        if confirmed_amount is not None:
            confirmed_total += confirmed_amount
            confirmed_count += 1
        else:
            pending_count += 1

        rows.append(
            {
                "batch_id": b.id,
                "contract_id": b.contract_id,
                "strategy": b.contract.strategy,
                "strategy_label": STRATEGY_LABEL.get(b.contract.strategy, b.contract.strategy),
                "service_time": b.service_time.isoformat(),
                "batch_status": b.status,
                "fact_status": fact.status if fact else None,
                "confirmed_amount": confirmed_amount,
                "pending_range": pending_range,
                "cross_tier": amount.cross_tier if amount else None,
                "publication": pub.downstream_ref if pub else None,
                "publication_state": pub.state if pub else None,
            }
        )
    return {
        "month": month,
        "batches": rows,
        "totals": {
            "confirmed_amount": round(confirmed_total, 2),
            "confirmed_count": confirmed_count,
            "pending_count": pending_count,
        },
    }
