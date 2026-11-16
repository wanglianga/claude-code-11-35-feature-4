"""任务合规判定：把排班承诺、车辆到场、处理场签收与投诉证据落到每个收运任务。

只有合同条款及证据支持的事项才判定违约：
- 无任何到场证据            → missed_collection（漏收）
- 到场晚于承诺+到场容差      → late_arrival（超时）
- 签收晚于承诺+签收时限      → late_receipt（迟到签收）
- 已核实且关联到本任务的投诉 → valid_complaint（未核投诉不扣分，只列风险）
承运商申辩被主管裁定 overturned 的违约类型从本任务撤销。
"""

from datetime import timedelta

from sqlalchemy.orm import Session

from ..models import (
    CarrierAppeal,
    Complaint,
    FacilitySignoff,
    ScheduledTask,
    SupervisorRuling,
    VehicleArrival,
)

DEFAULT_PENALTIES = {
    "missed_collection": 8.0,
    "late_arrival": 3.0,
    "late_receipt": 2.0,
    "valid_complaint": 5.0,
}

VIOLATION_LABELS = {
    "missed_collection": "漏收",
    "late_arrival": "到场超时",
    "late_receipt": "迟到签收",
    "valid_complaint": "核实投诉",
}


def penalties(contract) -> dict:
    return {**DEFAULT_PENALTIES, **((contract.sla_rules or {}).get("penalties") or {})}


def evaluate_task(db: Session, task: ScheduledTask) -> dict:
    """重算单个任务的合规快照（不写库），评分运行时逐任务调用。"""
    contract = task.contract
    rules = contract.sla_rules or {}
    arrival_tol = int(rules.get("on_time_arrival_minutes", 10))
    receipt_due_hours = int(rules.get("receipt_due_hours", 24))
    pen = penalties(contract)

    arrival = (
        db.query(VehicleArrival)
        .filter_by(task_id=task.id)
        .order_by(VehicleArrival.arrival_time)
        .first()
    )
    signoff = (
        db.query(FacilitySignoff)
        .filter_by(task_id=task.id)
        .order_by(FacilitySignoff.signoff_time.desc())
        .first()
    )

    arrival_info = None
    arrival_delay = None
    if arrival is not None:
        arrival_delay = round((arrival.arrival_time - task.scheduled_time).total_seconds() / 60.0, 1)
        arrival_info = {
            "event_id": arrival.id,
            "source_ref": arrival.source_ref,
            "arrival_time": arrival.arrival_time.isoformat(),
            "delay_minutes": arrival_delay,
            "content_hash": arrival.content_hash,
        }

    signoff_info = None
    receipt_delay_hours = None
    if signoff is not None:
        due = task.scheduled_time + timedelta(hours=receipt_due_hours)
        receipt_delay_hours = round((signoff.signoff_time - due).total_seconds() / 3600.0, 2)
        signoff_info = {
            "event_id": signoff.id,
            "source_ref": signoff.source_ref,
            "signoff_time": signoff.signoff_time.isoformat(),
            "due_time": due.isoformat(),
            "delay_hours": receipt_delay_hours,
            "content_hash": signoff.content_hash,
        }

    violations: list[dict] = []
    if arrival is None:
        violations.append(
            {
                "type": "missed_collection",
                "label": VIOLATION_LABELS["missed_collection"],
                "deduction": pen["missed_collection"],
                "clause": "承诺排班无任何车辆到场证据",
                "evidence": None,
            }
        )
    elif arrival_delay is not None and arrival_delay > arrival_tol:
        violations.append(
            {
                "type": "late_arrival",
                "label": VIOLATION_LABELS["late_arrival"],
                "deduction": pen["late_arrival"],
                "clause": f"到场容差 {arrival_tol} 分钟，实际延迟 {arrival_delay} 分钟",
                "evidence": arrival_info,
            }
        )

    if signoff is not None and receipt_delay_hours is not None and receipt_delay_hours > 0:
        violations.append(
            {
                "type": "late_receipt",
                "label": VIOLATION_LABELS["late_receipt"],
                "deduction": pen["late_receipt"],
                "clause": f"签收时限 {receipt_due_hours} 小时，实际超出 {receipt_delay_hours} 小时",
                "evidence": signoff_info,
            }
        )

    # ---- 投诉：未核不扣分；同组合并投诉只计一次；候选组合并列风险暂不计 ----
    complaint_rows, complaint_units = [], 0
    complaints = db.query(Complaint).filter_by(task_id=task.id).all()
    counted_groups: set[int] = set()
    for c in complaints:
        row = {
            "complaint_id": c.id,
            "status": c.status,
            "merge_group_id": c.merge_group_id,
            "reporter": c.reporter,
            "event_time": c.event_time.isoformat(),
            "counted": False,
            "reason": "",
        }
        if c.status != "verified":
            row["reason"] = "未核/已驳回投诉：只列风险，不扣分"
        elif c.merge_group_id is not None:
            group = c.merge_group
            if group.status == "confirmed":
                if c.merge_group_id not in counted_groups:
                    counted_groups.add(c.merge_group_id)
                    complaint_units += 1
                    row["counted"] = True
                    row["reason"] = f"合并组 #{c.merge_group_id} 已确认同一事件，整组计一次"
                else:
                    row["reason"] = f"与组合并，不重复计罚（组 #{c.merge_group_id}）"
            elif group.status == "candidate":
                row["reason"] = "合并候选待主管确认，暂不计罚"
            else:
                complaint_units += 1
                row["counted"] = True
                row["reason"] = "合并组已解除，按独立投诉计罚"
        else:
            complaint_units += 1
            row["counted"] = True
            row["reason"] = "独立核实投诉"
        complaint_rows.append(row)

    for _ in range(complaint_units):
        violations.append(
            {
                "type": "valid_complaint",
                "label": VIOLATION_LABELS["valid_complaint"],
                "deduction": pen["valid_complaint"],
                "clause": "已核实并关联到本任务的投诉（合并去重后）",
                "evidence": {"task_id": task.id},
            }
        )

    # ---- 申辩与主管裁定：overturned 撤销对应违约类型；upheld 维持 ----
    appeal_rows = []
    overturned_types: set[str] = set()
    appeals = db.query(CarrierAppeal).filter_by(task_id=task.id).order_by(CarrierAppeal.id).all()
    for a in appeals:
        ruling = db.query(SupervisorRuling).filter_by(appeal_id=a.id).first()
        row = {
            "appeal_id": a.id,
            "carrier_ref": a.carrier_ref,
            "violation_type": a.violation_type,
            "status": a.status,
            "ruling_id": ruling.id if ruling else None,
            "outcome": ruling.outcome if ruling else None,
            "ruling_note": ruling.ruling_note if ruling else None,
        }
        appeal_rows.append(row)
        if ruling is not None and ruling.outcome == "overturned":
            overturned_types.add(a.violation_type)

    applied_violations, removed_violations = [], []
    for v in violations:
        if v["type"] in overturned_types:
            removed_violations.append({**v, "deduction": 0.0, "removed_by": "主管裁定采纳申辩（overturned）"})
        else:
            applied_violations.append(v)

    risks = []
    if arrival is not None and signoff is None:
        risks.append({"type": "signoff_gap", "message": "已有到场证据但缺少处理场签收，签收时限证据缺口"})
    for row in complaint_rows:
        if not row["counted"] and row["status"] != "dismissed":
            risks.append(
                {
                    "type": "complaint_unverified" if row["status"] == "unverified" else "merge_pending",
                    "message": f"投诉 #{row['complaint_id']}：{row['reason']}",
                    "complaint_id": row["complaint_id"],
                }
            )

    deduction = round(sum(v["deduction"] for v in applied_violations), 2)
    snapshot = {
        "task_id": task.id,
        "contract_id": task.contract_id,
        "vehicle_id": task.vehicle_id,
        "address": task.address,
        "scheduled_time": task.scheduled_time.isoformat(),
        "task_status": task.status,
        "arrival": arrival_info,
        "signoff": signoff_info,
        "violations": applied_violations,
        "removed_violations": removed_violations,
        "complaints": complaint_rows,
        "appeals": appeal_rows,
        "deduction": deduction,
        "evidence_complete": arrival is not None and signoff is not None,
        "risks": risks,
    }
    task.compliance = snapshot
    return snapshot


def month_bounds(month: str):
    from datetime import datetime, timezone

    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    end = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=timezone.utc)
    return start, end


def eligible_tasks(db: Session, contract_id: str, month: str) -> list[ScheduledTask]:
    start, end = month_bounds(month)
    return (
        db.query(ScheduledTask)
        .filter(
            ScheduledTask.contract_id == contract_id,
            ScheduledTask.status == "eligible",
            ScheduledTask.scheduled_time >= start,
            ScheduledTask.scheduled_time < end,
        )
        .order_by(ScheduledTask.scheduled_time)
        .all()
    )
