"""计量裁定器：按合同策略签发计费重量 / 可接受区间、待核冲突与排除原因。

- priority：按合同约定的来源优先级，在满足时间/校准条件的来源中依序选定一份计费重量，
  其余合格来源作为差异佐证；无合格来源时形成待核冲突。
- interval：对满足质量门槛的多份重量签发可接受区间；区间重叠（或存在已批准的
  换算依据且换算后重叠）时形成计费事实，否则形成待核冲突。
- 任何来源都不会被直接当作无条件真值：均需通过质量门槛并保留排除/差异记录。
"""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (
    Batch,
    BatchLink,
    CalibrationRecord,
    MeasurementFact,
    SourceEvent,
)

MEASURE_LINK_TYPES = ("weighing", "receipt", "gate")
# 关联类型 → 证据来源类型 的映射（用于按合同优先级排序）
LINK_TO_SOURCE = {
    "weighing": "weighbridge_reading",
    "receipt": "facility_receipt",
    "gate": "gate_record",
}


def _quality_check(db: Session, event: SourceEvent, ref_time, tolerance_minutes: int):
    """返回 (ok, adjusted_weight, calibration_ref, reasons)。

    校准更正处理：事件记录时的校准版本若已被更正，则适用后继版本的换算系数，
    并在校准引用中留痕；无后继版本时视为校准争议，排除该来源。
    """
    reasons = []
    cal_ref = None
    weight = event.payload.get("weight_tons")
    if weight is None:
        reasons.append("缺少重量字段 weight_tons")
        return False, None, None, reasons

    if ref_time is not None:
        delta = abs((event.event_time - ref_time).total_seconds()) / 60.0
        if delta > tolerance_minutes:
            reasons.append(
                f"事件时间与任务时间相差 {delta:.0f} 分钟，超出合同容差 {tolerance_minutes} 分钟"
            )

    adjusted = float(weight)
    if event.device_id and event.calibration_version is not None:
        cal = (
            db.query(CalibrationRecord)
            .filter_by(device_id=event.device_id, version=event.calibration_version)
            .first()
        )
        if cal is None:
            reasons.append(
                f"设备 {event.device_id} 校准版本 v{event.calibration_version} 无记录"
            )
        else:
            cal_ref = {
                "device_id": cal.device_id,
                "version": cal.version,
                "status": cal.status,
                "factor": cal.factor,
            }
            if cal.status == "corrected":
                successor = db.get(CalibrationRecord, cal.corrected_by_id) if cal.corrected_by_id else None
                if successor is None:
                    reasons.append(
                        f"设备 {cal.device_id} 校准 v{cal.version} 存在更正争议且无后继版本"
                    )
                else:
                    adjusted = round(adjusted * successor.factor, 4)
                    cal_ref["corrected_to"] = {
                        "version": successor.version,
                        "factor": successor.factor,
                        "adjusted_weight": adjusted,
                    }
            elif cal.factor != 1.0:
                adjusted = round(adjusted * cal.factor, 4)

    return (len(reasons) == 0), adjusted, cal_ref, reasons


def _next_version(db: Session, batch_id: str) -> int:
    latest = (
        db.query(MeasurementFact)
        .filter_by(batch_id=batch_id)
        .order_by(MeasurementFact.version.desc())
        .first()
    )
    return (latest.version + 1) if latest else 1


def adjudicate(db: Session, batch: Batch) -> MeasurementFact:
    contract = batch.contract
    links = db.query(BatchLink).filter_by(batch_id=batch.id).all()
    confirmed = [l for l in links if l.status == "confirmed"]

    task_event = None
    for l in confirmed:
        if l.link_type == "task":
            task_event = db.get(SourceEvent, l.event_id)
            break
    ref_time = task_event.event_time if task_event else batch.service_time

    # 收集候选计量来源并逐一过质量门槛
    candidates, exclusions = [], []
    for l in confirmed:
        if l.link_type not in MEASURE_LINK_TYPES:
            continue
        event = db.get(SourceEvent, l.event_id)
        ok, adjusted, cal_ref, reasons = _quality_check(
            db, event, ref_time, contract.time_tolerance_minutes
        )
        item = {
            "link_id": l.id,
            "event_id": event.id,
            "source_type": event.source_type,
            "weight": event.payload.get("weight_tons"),
            "adjusted_weight": adjusted,
            "event_time": event.event_time.isoformat(),
            "device_id": event.device_id,
            "site_id": event.site_id,
        }
        if cal_ref:
            item["calibration"] = cal_ref
        if ok:
            candidates.append(item)
        else:
            exclusions.append({**item, "reasons": reasons})

    # 冻结中的候选关联也记入排除原因，保证可审计
    for l in links:
        if l.link_type in MEASURE_LINK_TYPES and l.status == "frozen":
            exclusions.append(
                {"link_id": l.id, "event_id": l.event_id, "reasons": ["关联冻结中：称重记录争用待解"]}
            )

    calibration_refs = [c["calibration"] for c in candidates if c.get("calibration")]
    if contract.strategy == "priority":
        fact = _adjudicate_priority(db, batch, contract, candidates, exclusions, calibration_refs)
    else:
        fact = _adjudicate_interval(db, batch, contract, candidates, exclusions, calibration_refs)

    db.add(fact)
    batch.status = "adjudicated" if fact.status == "issued" else "open"
    db.commit()
    db.refresh(fact)
    return fact


def _adjudicate_priority(db, batch, contract, candidates, exclusions, calibration_refs):
    priority = contract.source_priority or []
    selected, discrepancy = None, []
    for source_type in priority:
        group = [c for c in candidates if c["source_type"] == source_type]
        if not group:
            continue
        if selected is None:
            selected = group[0]
        else:
            discrepancy.extend(group)
    # 未列入优先级表的合格来源一律作为差异佐证
    listed = {c["event_id"] for c in ([selected] if selected else []) } | {d["event_id"] for d in discrepancy}
    for c in candidates:
        if c["event_id"] not in listed:
            discrepancy.append(c)

    version = _next_version(db, batch.id)
    if selected is None:
        return MeasurementFact(
            batch_id=batch.id,
            version=version,
            strategy="priority",
            status="pending_conflict",
            conflicts=[
                {
                    "type": "no_qualified_source",
                    "message": "主计量来源缺失或校准争议：优先级链上没有来源通过时间/校准条件",
                    "excluded_count": len(exclusions),
                }
            ],
            exclusions=exclusions,
            calibration_refs=calibration_refs,
        )

    sel_w = selected["adjusted_weight"]
    disc = [
        {
            **{k: d[k] for k in ("event_id", "source_type", "weight", "adjusted_weight", "event_time", "device_id", "site_id")},
            "diff_kg": round((d["adjusted_weight"] - sel_w) * 1000, 1),
            "diff_pct": round((d["adjusted_weight"] - sel_w) / sel_w * 100, 3) if sel_w else None,
        }
        for d in discrepancy
    ]
    return MeasurementFact(
        batch_id=batch.id,
        version=version,
        strategy="priority",
        status="issued",
        billing_weight=sel_w,
        selected_event_id=selected["event_id"],
        discrepancy=disc,
        conflicts=[],
        exclusions=exclusions,
        calibration_refs=calibration_refs,
        note=f"依合同优先级选定 {selected['source_type']}（事件 #{selected['event_id']}）为计费重量",
    )


def _adjudicate_interval(db, batch, contract, candidates, exclusions, calibration_refs):
    tol = contract.quality_tolerance_pct
    version = _next_version(db, batch.id)

    def build_intervals(items):
        return [
            {
                **item,
                "low": round(item["adjusted_weight"] * (1 - tol), 6),
                "high": round(item["adjusted_weight"] * (1 + tol), 6),
            }
            for item in items
        ]

    def overlap(intervals):
        low = max(i["low"] for i in intervals)
        high = min(i["high"] for i in intervals)
        return (low, high) if low <= high else (None, None)

    intervals = build_intervals(candidates)
    conversion_note = None
    low = high = None
    if len(intervals) >= 1:
        low, high = overlap(intervals)

    if low is None and len(intervals) >= 2 and contract.approved_conversion:
        # 已批准的换算依据：以优先级首位来源为基准，其余来源按批准系数换算后重算重叠
        factor = contract.approved_conversion["factor"]
        order = {s: i for i, s in enumerate(contract.source_priority or [])}
        ranked = sorted(candidates, key=lambda c: order.get(c["source_type"], 99))
        base = ranked[0]
        converted = [base]
        for c in ranked[1:]:
            converted.append({**c, "adjusted_weight": round(c["adjusted_weight"] * factor, 4)})
        intervals = build_intervals(converted)
        low, high = overlap(intervals)
        if low is not None:
            conversion_note = (
                f"区间不重叠，依已批准换算依据（×{factor}，{contract.approved_conversion.get('note', '')}）"
                f"换算后重叠"
            )

    if low is None:
        conflicts = []
        if len(candidates) < 1:
            conflicts.append(
                {"type": "no_qualified_source", "message": "无来源通过质量门槛", "excluded_count": len(exclusions)}
            )
        else:
            conflicts.append(
                {
                    "type": "interval_disjoint",
                    "message": "多份重量的可接受区间互不重叠，且无可用的已批准换算依据",
                    "intervals": [
                        {"event_id": i["event_id"], "source_type": i["source_type"], "low": i["low"], "high": i["high"]}
                        for i in intervals
                    ],
                }
            )
        return MeasurementFact(
            batch_id=batch.id,
            version=version,
            strategy="interval",
            status="pending_conflict",
            conflicts=conflicts,
            exclusions=exclusions,
            calibration_refs=calibration_refs,
        )

    return MeasurementFact(
        batch_id=batch.id,
        version=version,
        strategy="interval",
        status="issued",
        billing_weight=None,
        acceptable_low=low,
        acceptable_high=high,
        discrepancy=[
            {"event_id": i["event_id"], "source_type": i["source_type"], "low": i["low"], "high": i["high"]}
            for i in intervals
        ],
        conflicts=[],
        exclusions=exclusions,
        calibration_refs=calibration_refs,
        note=conversion_note or f"{len(intervals)} 份合格重量区间重叠，签发可接受区间 [{low}, {high}]",
    )


def to_dict(f: MeasurementFact) -> dict:
    return {
        "id": f.id,
        "batch_id": f.batch_id,
        "version": f.version,
        "strategy": f.strategy,
        "status": f.status,
        "billing_weight": f.billing_weight,
        "acceptable_low": f.acceptable_low,
        "acceptable_high": f.acceptable_high,
        "selected_event_id": f.selected_event_id,
        "discrepancy": f.discrepancy,
        "conflicts": f.conflicts,
        "exclusions": f.exclusions,
        "calibration_refs": f.calibration_refs,
        "note": f.note,
        "created_at": f.created_at.isoformat(),
    }
