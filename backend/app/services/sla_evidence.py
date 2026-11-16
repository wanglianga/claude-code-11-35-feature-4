"""SLA 证据服务：排班承诺、车辆到场、处理场签收、居民投诉的原文留存与内容哈希。

与计量域证据库一致：事件时间归一化 UTC 后计算 SHA-256，入库前后可重算核对。
"""

import hashlib
import json
from datetime import timezone

from sqlalchemy.orm import Session

from ..models import (
    Complaint,
    ComplaintMergeGroup,
    FacilitySignoff,
    ScheduledTask,
    VehicleArrival,
)


def content_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_task(
    db: Session,
    *,
    task_id: str,
    contract_id: str,
    vehicle_id: str,
    address: str,
    scheduled_time,
    latitude: float | None = None,
    longitude: float | None = None,
) -> ScheduledTask:
    if db.get(ScheduledTask, task_id):
        from fastapi import HTTPException

        raise HTTPException(409, "排班任务号已存在")
    task = ScheduledTask(
        id=task_id,
        contract_id=contract_id,
        vehicle_id=vehicle_id,
        address=address,
        latitude=latitude,
        longitude=longitude,
        scheduled_time=scheduled_time,
    )
    _apply_eligibility(db, task)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _apply_eligibility(db: Session, task: ScheduledTask) -> None:
    """评分资格：只有已确认合同窗口内的任务才进入评分，其余记录排除原因。"""
    from ..models import Contract

    contract = db.get(Contract, task.contract_id)
    if contract is None:
        task.status = "excluded"
        task.exclude_reason = "合同不存在"
    elif not contract.confirmed:
        task.status = "excluded"
        task.exclude_reason = f"合同 {contract.id} 尚未确认，任务不进入评分"
    else:
        task.status = "eligible"
        task.exclude_reason = ""


def create_arrival(
    db: Session,
    *,
    vehicle_id: str,
    address: str,
    arrival_time,
    source_ref: str = "",
    raw_text: str = "",
) -> VehicleArrival:
    h = content_hash(
        {
            "kind": "vehicle_arrival",
            "vehicle_id": vehicle_id,
            "address": address,
            "arrival_time": _utc(arrival_time).isoformat(),
            "source_ref": source_ref,
            "raw_text": raw_text,
        }
    )
    arrival = VehicleArrival(
        vehicle_id=vehicle_id,
        address=address,
        arrival_time=arrival_time,
        source_ref=source_ref,
        raw_text=raw_text,
        content_hash=h,
    )
    db.add(arrival)
    db.flush()
    arrival.task_id = match_task(db, arrival)
    db.commit()
    db.refresh(arrival)
    return arrival


def match_task(db: Session, arrival: VehicleArrival) -> str | None:
    """按车 + 地址 + 时间窗（排班当日 ±1 天）把到场证据匹配到真正受影响的收运任务。"""
    candidates = (
        db.query(ScheduledTask)
        .filter(
            ScheduledTask.vehicle_id == arrival.vehicle_id,
            ScheduledTask.address == arrival.address,
        )
        .all()
    )
    best, best_gap = None, None
    for t in candidates:
        gap = abs((arrival.arrival_time - t.scheduled_time).total_seconds())
        if gap <= 24 * 3600 and (best_gap is None or gap < best_gap):
            best, best_gap = t, gap
    return best.id if best else None


def create_signoff(
    db: Session,
    *,
    task_id: str,
    vehicle_id: str,
    signoff_time,
    site_id: str | None = None,
    source_ref: str = "",
    raw_text: str = "",
    weight_tons: float | None = None,
) -> FacilitySignoff:
    if db.get(ScheduledTask, task_id) is None:
        from fastapi import HTTPException

        raise HTTPException(404, "排班任务不存在，无法挂接签收证据")
    h = content_hash(
        {
            "kind": "facility_signoff",
            "task_id": task_id,
            "vehicle_id": vehicle_id,
            "signoff_time": _utc(signoff_time).isoformat(),
            "site_id": site_id,
            "source_ref": source_ref,
            "weight_tons": weight_tons,
            "raw_text": raw_text,
        }
    )
    signoff = FacilitySignoff(
        task_id=task_id,
        vehicle_id=vehicle_id,
        signoff_time=signoff_time,
        site_id=site_id,
        source_ref=source_ref,
        raw_text=raw_text,
        weight_tons=weight_tons,
        content_hash=h,
    )
    db.add(signoff)
    db.commit()
    db.refresh(signoff)
    return signoff


def create_complaint(
    db: Session,
    *,
    reporter: str,
    address: str,
    event_time,
    category: str = "",
    description: str = "",
    raw_text: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
) -> Complaint:
    h = content_hash(
        {
            "kind": "complaint",
            "reporter": reporter,
            "address": address,
            "event_time": _utc(event_time).isoformat(),
            "category": category,
            "description": description,
            "latitude": latitude,
            "longitude": longitude,
            "raw_text": raw_text,
        }
    )
    complaint = Complaint(
        reporter=reporter,
        address=address,
        event_time=event_time,
        category=category,
        description=description,
        raw_text=raw_text,
        latitude=latitude,
        longitude=longitude,
        content_hash=h,
    )
    db.add(complaint)
    db.flush()
    group = find_merge_group(db, complaint)
    if group is not None:
        complaint.merge_group_id = group.id
    db.commit()
    db.refresh(complaint)
    return complaint


def find_merge_group(db: Session, complaint: Complaint) -> ComplaintMergeGroup | None:
    """合并候选：同地址且事件时间落在合同合并窗口内的投诉归入同一候选组。"""
    from ..models import Contract

    window_minutes = 120
    groups = (
        db.query(ComplaintMergeGroup)
        .filter(ComplaintMergeGroup.status.in_(["candidate", "confirmed"]))
        .all()
    )
    for g in groups:
        if g.address != complaint.address:
            continue
        gap = abs((complaint.event_time - g.event_time).total_seconds()) / 60.0
        if gap <= window_minutes:
            return g
    return None


def _utc(value):
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value
