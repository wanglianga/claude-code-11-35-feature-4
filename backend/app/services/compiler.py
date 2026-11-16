"""费率编译器：仅在冻结目录上生成金额事实；解释版本递增，历史不回写。

- 目录选择：以服务时间当时有效的冻结目录为准；目录在服务后修订时，
  重新编译只产生新的解释版本，仍引用当时有效目录，不回写历史金额。
- 区间裁定：上下界分别编译边界金额；跨越费用阶梯时标记 cross_tier，
  不取中点伪造已确认金额。
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import AmountFact, Batch, MeasurementFact, RateCatalog


def tiered_amount(weight: float, tiers: list[dict]) -> float:
    """累进阶梯计价（吨 × 元/吨）。"""
    amount, prev = 0.0, 0.0
    for tier in sorted(tiers, key=lambda t: float("inf") if t["up_to"] is None else t["up_to"]):
        up_to, rate = tier["up_to"], float(tier["rate"])
        if up_to is None:
            amount += (weight - prev) * rate
            break
        if weight > up_to:
            amount += (up_to - prev) * rate
            prev = up_to
        else:
            amount += (weight - prev) * rate
            break
    return round(amount, 2)


def _tier_index(weight: float, tiers: list[dict]) -> int:
    ordered = sorted(tiers, key=lambda t: float("inf") if t["up_to"] is None else t["up_to"])
    for i, tier in enumerate(ordered):
        if tier["up_to"] is None or weight <= tier["up_to"]:
            return i
    return len(ordered) - 1


def catalog_at(db: Session, service_time) -> RateCatalog | None:
    return (
        db.query(RateCatalog)
        .filter(RateCatalog.effective_from <= service_time)
        .filter((RateCatalog.effective_to.is_(None)) | (RateCatalog.effective_to > service_time))
        .order_by(RateCatalog.version.desc())
        .first()
    )


def compile_amounts(db: Session, batch: Batch) -> AmountFact:
    fact = (
        db.query(MeasurementFact)
        .filter_by(batch_id=batch.id)
        .order_by(MeasurementFact.version.desc())
        .first()
    )
    if fact is None:
        raise HTTPException(409, "批次尚未完成计量裁定")
    if fact.status == "under_review":
        raise HTTPException(409, "计量事实复审中，暂停编译新金额")
    if fact.status != "issued":
        raise HTTPException(409, "计量事实未签发（存在待核冲突），不能编译金额")

    catalog = catalog_at(db, batch.service_time)
    if catalog is None:
        raise HTTPException(409, "服务时间无有效费率目录")
    if not catalog.frozen:
        raise HTTPException(409, f"费率目录 v{catalog.version} 未冻结，不能用于编译")

    previous = (
        db.query(AmountFact)
        .filter_by(batch_id=batch.id, status="issued")
        .order_by(AmountFact.interpretation_version.desc())
        .all()
    )
    version = (previous[0].interpretation_version + 1) if previous else 1
    for p in previous:  # 旧解释版本标记作废，内容不回写
        p.status = "superseded"

    if fact.billing_weight is not None:
        amount_fact = AmountFact(
            batch_id=batch.id,
            measurement_fact_id=fact.id,
            interpretation_version=version,
            catalog_id=catalog.id,
            catalog_version=catalog.version,
            weight_used=fact.billing_weight,
            amount=tiered_amount(fact.billing_weight, catalog.tiers),
            cross_tier=False,
        )
    else:
        low_amt = tiered_amount(fact.acceptable_low, catalog.tiers)
        high_amt = tiered_amount(fact.acceptable_high, catalog.tiers)
        cross = _tier_index(fact.acceptable_low, catalog.tiers) != _tier_index(
            fact.acceptable_high, catalog.tiers
        )
        amount_fact = AmountFact(
            batch_id=batch.id,
            measurement_fact_id=fact.id,
            interpretation_version=version,
            catalog_id=catalog.id,
            catalog_version=catalog.version,
            weight_used=None,
            amount=None,  # 区间裁定不取中点，已确认金额留空
            amount_low=low_amt,
            amount_high=high_amt,
            cross_tier=cross,
        )
    db.add(amount_fact)
    db.commit()
    db.refresh(amount_fact)
    return amount_fact


def to_dict(a: AmountFact) -> dict:
    return {
        "id": a.id,
        "batch_id": a.batch_id,
        "measurement_fact_id": a.measurement_fact_id,
        "interpretation_version": a.interpretation_version,
        "catalog_id": a.catalog_id,
        "catalog_version": a.catalog_version,
        "weight_used": a.weight_used,
        "amount": a.amount,
        "amount_low": a.amount_low,
        "amount_high": a.amount_high,
        "cross_tier": a.cross_tier,
        "status": a.status,
        "created_at": a.created_at.isoformat(),
    }
