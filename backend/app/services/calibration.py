"""校准更正：受影响计量事实转为复审中；新计费视图暂停引用，旧账务视图保留原依据。"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (
    Batch,
    BatchLink,
    CalibrationRecord,
    MeasurementFact,
    SourceEvent,
)


def correct_calibration(db: Session, cal: CalibrationRecord, factor: float, note: str = ""):
    if cal.status != "active":
        raise HTTPException(409, f"校准记录 v{cal.version} 已处于 {cal.status} 状态，不能重复更正")

    new_version = (
        db.query(CalibrationRecord)
        .filter_by(device_id=cal.device_id)
        .order_by(CalibrationRecord.version.desc())
        .first()
    )
    successor = CalibrationRecord(
        device_id=cal.device_id,
        version=(new_version.version + 1) if new_version else 1,
        effective_from=datetime.now(timezone.utc),
        factor=factor,
        status="active",
        note=note or f"更正 v{cal.version} 的计量偏差",
    )
    db.add(successor)
    db.flush()
    cal.status = "corrected"
    cal.corrected_by_id = successor.id

    # 受影响事件 → 关联批次 → 最新计量事实转为复审中
    events = (
        db.query(SourceEvent)
        .filter_by(device_id=cal.device_id, calibration_version=cal.version)
        .all()
    )
    event_ids = [e.id for e in events]
    affected_batches: set[str] = set()
    if event_ids:
        links = db.query(BatchLink).filter(BatchLink.event_id.in_(event_ids)).all()
        for link in links:
            fact = (
                db.query(MeasurementFact)
                .filter_by(batch_id=link.batch_id)
                .order_by(MeasurementFact.version.desc())
                .first()
            )
            if fact and fact.status in ("issued", "pending_conflict"):
                fact.status = "under_review"
                fact.conflicts = (fact.conflicts or []) + [
                    {
                        "type": "calibration_corrected",
                        "message": (
                            f"地磅 {cal.device_id} 校准 v{cal.version} 已更正为 "
                            f"v{successor.version}（×{factor}），计量事实转入复审"
                        ),
                    }
                ]
                affected_batches.add(link.batch_id)

    for batch_id in affected_batches:
        batch = db.get(Batch, batch_id)
        if batch:
            batch.status = "under_review"

    db.commit()
    db.refresh(successor)
    return successor, sorted(affected_batches)
