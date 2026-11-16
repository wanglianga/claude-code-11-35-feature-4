"""差异发布器：输出计费依据、未决范围与下游引用版本。

- 计量事实复审中：新计费视图暂停引用（paused），旧账务视图（历史发布）保留原依据。
- 区间裁定：同时发布计费依据与未决范围；跨阶梯时边界金额分别列示，不取中点。
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import AmountFact, Batch, MeasurementFact, Publication


def publish(db: Session, batch: Batch) -> Publication:
    fact = (
        db.query(MeasurementFact)
        .filter_by(batch_id=batch.id)
        .order_by(MeasurementFact.version.desc())
        .first()
    )
    if fact is None:
        raise HTTPException(409, "批次尚未完成计量裁定，不能发布")

    latest_pub = (
        db.query(Publication)
        .filter_by(batch_id=batch.id)
        .order_by(Publication.version.desc())
        .first()
    )
    version = (latest_pub.version + 1) if latest_pub else 1
    downstream_ref = f"PUB-{batch.id}-v{version}"

    if fact.status == "under_review":
        pub = Publication(
            batch_id=batch.id,
            version=version,
            strategy=fact.strategy,
            state="paused",
            billing_basis=None,
            pending_ranges={"reason": "计量事实复审中，新计费视图暂停引用；历史账务视图保留原依据"},
            downstream_ref=downstream_ref,
            note="校准更正触发复审",
        )
    elif fact.status == "pending_conflict":
        pub = Publication(
            batch_id=batch.id,
            version=version,
            strategy=fact.strategy,
            state="pending",
            billing_basis=None,
            pending_ranges={"conflicts": fact.conflicts, "exclusions": fact.exclusions},
            downstream_ref=downstream_ref,
            note="存在待核冲突，暂无计费依据",
        )
    else:
        amount = (
            db.query(AmountFact)
            .filter_by(batch_id=batch.id, measurement_fact_id=fact.id, status="issued")
            .order_by(AmountFact.interpretation_version.desc())
            .first()
        )
        if amount is None:
            raise HTTPException(409, "当前计量事实尚未编译金额，请先执行费率编译")
        billing_basis = {
            "contract_id": batch.contract_id,
            "strategy": fact.strategy,
            "measurement_fact_id": fact.id,
            "measurement_version": fact.version,
            "selected_event_id": fact.selected_event_id,
            "billing_weight": fact.billing_weight,
            "acceptable_low": fact.acceptable_low,
            "acceptable_high": fact.acceptable_high,
            "amount_fact_id": amount.id,
            "interpretation_version": amount.interpretation_version,
            "catalog_id": amount.catalog_id,
            "catalog_version": amount.catalog_version,
            "amount": amount.amount,
            "amount_low": amount.amount_low,
            "amount_high": amount.amount_high,
            "cross_tier": amount.cross_tier,
            "calibration_refs": fact.calibration_refs,
        }
        pending = None
        if amount.amount is None:
            pending = {
                "amount_low": amount.amount_low,
                "amount_high": amount.amount_high,
                "cross_tier": amount.cross_tier,
                "note": "区间裁定：边界金额分别计算并列示，不取中点伪造已确认金额",
            }
        pub = Publication(
            batch_id=batch.id,
            version=version,
            strategy=fact.strategy,
            state="published",
            billing_basis=billing_basis,
            pending_ranges=pending,
            downstream_ref=downstream_ref,
        )

    db.add(pub)
    if pub.state == "published":
        batch.status = "published"
    db.commit()
    db.refresh(pub)
    return pub


def to_dict(p: Publication) -> dict:
    return {
        "id": p.id,
        "batch_id": p.batch_id,
        "version": p.version,
        "strategy": p.strategy,
        "state": p.state,
        "billing_basis": p.billing_basis,
        "pending_ranges": p.pending_ranges,
        "downstream_ref": p.downstream_ref,
        "note": p.note,
        "created_at": p.created_at.isoformat(),
    }
