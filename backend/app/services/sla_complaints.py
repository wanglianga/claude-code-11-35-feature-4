"""投诉合并与核实。

- 合并候选：按地址（文本或经纬度距离）、事件时间窗与事件关系形成候选组；
  同一问题被多人报告时只计一次罚分，不能因多人报告重复计罚。
- 未核投诉只列风险，不直接扣分；核实后关联到真正受影响的收运任务才进入评分。
"""

import math
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (
    Complaint,
    ComplaintMergeGroup,
    Contract,
    ScheduledTask,
)


def _haversine_meters(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _merge_window_minutes(db: Session) -> int:
    """合并窗口取各合同条款配置的最小值（默认 120 分钟）。"""
    windows = [
        (c.sla_rules or {}).get("complaint_merge_window_minutes")
        for c in db.query(Contract).all()
    ]
    windows = [w for w in windows if w]
    return min(windows) if windows else 120


def _merge_radius_meters(db: Session) -> int:
    radii = [
        (c.sla_rules or {}).get("complaint_radius_meters")
        for c in db.query(Contract).all()
    ]
    radii = [r for r in radii if r]
    return min(radii) if radii else 300


def _same_place(a: Complaint, b: Complaint, radius: int) -> tuple[bool, str]:
    if a.address and a.address == b.address:
        return True, "same_address"
    if None not in (a.latitude, a.longitude, b.latitude, b.longitude):
        if _haversine_meters(a.latitude, a.longitude, b.latitude, b.longitude) <= radius:
            return True, "geo_radius"
    return False, ""


def detect_merges(db: Session) -> list[ComplaintMergeGroup]:
    """扫描全部投诉，为满足地址+时间窗关系的投诉创建/维护候选合并组。幂等。"""
    window = _merge_window_minutes(db)
    radius = _merge_radius_meters(db)
    complaints = db.query(Complaint).order_by(Complaint.event_time).all()
    created: list[ComplaintMergeGroup] = []

    for c in complaints:
        if c.merge_group_id is not None:
            continue
        # ① 优先并入已存在的候选/确认组合（同地址且在时间窗内），支持三人以上同报
        existing = (
            db.query(ComplaintMergeGroup)
            .filter(ComplaintMergeGroup.status.in_(["candidate", "confirmed"]))
            .order_by(ComplaintMergeGroup.id)
            .all()
        )
        for g in existing:
            if g.address == c.address and abs((c.event_time - g.event_time).total_seconds()) / 60.0 <= window:
                c.merge_group_id = g.id
                break
        if c.merge_group_id is not None:
            continue
        # ② 否则与另一条未分组投诉两两配对，生成新的候选组合
        for other in complaints:
            if other.id == c.id or other.merge_group_id is not None:
                continue
            same, relation = _same_place(c, other, radius)
            if not same:
                continue
            gap = abs((c.event_time - other.event_time).total_seconds()) / 60.0
            if gap > window:
                continue
            group = ComplaintMergeGroup(
                status="candidate",
                address=c.address,
                event_time=min(c.event_time, other.event_time),
                relation="time_window" if relation == "same_address" else relation,
                reason=(
                    f"同地址且事件时间相差 {gap:.0f} 分钟（窗口 {window} 分钟），"
                    "系统形成合并候选，待主管确认是否同一事件"
                ),
            )
            db.add(group)
            db.flush()
            c.merge_group_id = group.id
            other.merge_group_id = group.id
            created.append(group)
            break
    db.commit()
    for g in created:
        db.refresh(g)
    return created


def decide_group(db: Session, group: ComplaintMergeGroup, *, confirm: bool, reason: str, username: str):
    if group.status not in ("candidate",):
        raise HTTPException(409, "合并组已处理，不能重复裁定")
    group.status = "confirmed" if confirm else "dismissed"
    group.decided_by = username
    group.decided_at = datetime.now(timezone.utc)
    group.reason = reason or group.reason
    if not confirm:
        # 并非同一事件：解除合并关系，各投诉独立参与后续核实
        for c in db.query(Complaint).filter_by(merge_group_id=group.id).all():
            c.merge_group_id = None
    db.commit()
    db.refresh(group)
    return group


def verify_complaint(
    db: Session, complaint: Complaint, *, task_id: str | None, note: str, username: str
) -> Complaint:
    if complaint.status == "dismissed":
        raise HTTPException(409, "已驳回的投诉需先恢复才能核实")
    task = db.get(ScheduledTask, task_id) if task_id else None
    if task_id and task is None:
        raise HTTPException(404, "关联的收运任务不存在")
    complaint.status = "verified"
    complaint.task_id = task_id
    complaint.verified_by = username
    complaint.verified_at = datetime.now(timezone.utc)
    complaint.verify_note = note or "主管核实：投诉成立"
    db.commit()
    db.refresh(complaint)
    return complaint


def dismiss_complaint(db: Session, complaint: Complaint, *, note: str, username: str) -> Complaint:
    complaint.status = "dismissed"
    complaint.verified_by = username
    complaint.verified_at = datetime.now(timezone.utc)
    complaint.verify_note = note or "主管核实：投诉不成立"
    db.commit()
    db.refresh(complaint)
    return complaint


def group_to_dict(g: ComplaintMergeGroup) -> dict:
    return {
        "id": g.id,
        "status": g.status,
        "address": g.address,
        "event_time": g.event_time.isoformat(),
        "relation": g.relation,
        "reason": g.reason,
        "decided_by": g.decided_by,
        "decided_at": g.decided_at.isoformat() if g.decided_at else None,
    }


def complaint_to_dict(c: Complaint) -> dict:
    return {
        "id": c.id,
        "reporter": c.reporter,
        "address": c.address,
        "latitude": c.latitude,
        "longitude": c.longitude,
        "event_time": c.event_time.isoformat(),
        "category": c.category,
        "description": c.description,
        "raw_text": c.raw_text,
        "content_hash": c.content_hash,
        "status": c.status,
        "merge_group_id": c.merge_group_id,
        "task_id": c.task_id,
        "verified_by": c.verified_by,
        "verified_at": c.verified_at.isoformat() if c.verified_at else None,
        "verify_note": c.verify_note,
        "created_at": c.created_at.isoformat(),
    }
