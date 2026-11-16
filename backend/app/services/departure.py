"""签收后离场核查：不能仅以最后定位晚于签收认定矛盾。

必须比较：① 支持该批次到场的定位片段；② 各片段的时钟校正（设备时钟偏移）；
③ 合同/核查容差。全部晚于签收的定位都能被时钟校正解释且落在容差内，
且存在支持到场的片段时，判定 consistent。
"""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Batch, BatchLink, DepartureCheck, PositionSegment, SourceEvent


def check_departure(db: Session, batch: Batch, tolerance_minutes: int = 10) -> DepartureCheck:
    receipt_link = (
        db.query(BatchLink)
        .filter_by(batch_id=batch.id, link_type="receipt", status="confirmed")
        .first()
    )
    if receipt_link is None:
        raise HTTPException(409, "批次无已确认的签收记录，无法执行离场核查")
    receipt = db.get(SourceEvent, receipt_link.event_id)
    receipt_time = receipt.event_time
    tol = timedelta(minutes=tolerance_minutes)

    segments = (
        db.query(PositionSegment)
        .filter_by(vehicle_id=batch.vehicle_id)
        .order_by(PositionSegment.end_time)
        .all()
    )

    supporting, late_positions, evaluated = [], [], []
    for s in segments:
        offset = timedelta(seconds=s.clock_offset_seconds)
        corr_start, corr_end = s.start_time - offset, s.end_time - offset
        evaluated.append(
            {
                "segment_id": s.id,
                "site_id": s.site_id,
                "raw_start": s.start_time.isoformat(),
                "raw_end": s.end_time.isoformat(),
                "clock_offset_seconds": s.clock_offset_seconds,
                "corrected_start": corr_start.isoformat(),
                "corrected_end": corr_end.isoformat(),
            }
        )
        # 支持到场的片段：校正后在签收时间前后窗口内处于签收场地
        if (
            s.site_id == receipt.site_id
            and corr_start <= receipt_time + tol
            and corr_end >= receipt_time - 3 * tol
        ):
            supporting.append(s.id)
        # 原始时间上晚于签收的定位：必须经时钟校正后仍超出容差才算矛盾
        if s.end_time > receipt_time:
            late_positions.append(
                {
                    "segment_id": s.id,
                    "raw_end": s.end_time.isoformat(),
                    "corrected_end": corr_end.isoformat(),
                    "explained_by_clock": corr_end <= receipt_time + tol,
                }
            )

    unexplained = [p for p in late_positions if not p["explained_by_clock"]]
    if not supporting:
        result = "insufficient"
        message = "缺少支持该批次到场的定位片段，证据不足，不能据此认定矛盾或一致"
    elif unexplained:
        result = "contradiction"
        message = f"{len(unexplained)} 条定位经时钟校正后仍晚于签收且超出容差 {tolerance_minutes} 分钟"
    else:
        result = "consistent"
        message = (
            f"最后定位晚于签收系设备时钟偏移所致（已按偏移量校正，容差 {tolerance_minutes} 分钟），"
            f"且存在 {len(supporting)} 条支持到场的片段，不认定矛盾"
        )

    check = DepartureCheck(
        batch_id=batch.id,
        result=result,
        detail={
            "receipt_event_id": receipt.id,
            "receipt_time": receipt_time.isoformat(),
            "receipt_site_id": receipt.site_id,
            "tolerance_minutes": tolerance_minutes,
            "supporting_segment_ids": supporting,
            "late_positions": late_positions,
            "segments_evaluated": evaluated,
            "message": message,
        },
    )
    db.add(check)
    db.commit()
    db.refresh(check)
    return check


def to_dict(c: DepartureCheck) -> dict:
    return {
        "id": c.id,
        "batch_id": c.batch_id,
        "result": c.result,
        "detail": c.detail,
        "created_at": c.created_at.isoformat(),
    }
