"""月度 SLA 评分：两种核算模式，同一合同窗口与任务证据。

- incremental（增量更新）：任务证据到达即可重算，较早反映纠正结果；同月分数会多次变化，
  每个版本都记录受影响车次，可逐版本追溯。
- month_end（月结统一）：等月结证据批次锁定后统一计算，结果稳定、便于复核；
  月内只展示进度与缺口；月结锁定后的迟到签收等追溯变更只产生调整单，旧付款不改写。

两种模式共用同一套合规判定（sla_adherence）与合同条款，保证口径一致。
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (
    Complaint,
    Contract,
    FacilitySignoff,
    LockAdjustment,
    MonthlyScore,
    ScoreVersion,
    ScheduledTask,
)
from . import sla_adherence


# ---------------------------------------------------------------- 读取助手


def _month_of(dt_value) -> str:
    return dt_value.strftime("%Y-%m")


def get_score(db: Session, contract_id: str, month: str, mode: str) -> MonthlyScore | None:
    return (
        db.query(MonthlyScore)
        .filter_by(contract_id=contract_id, month=month, mode=mode)
        .first()
    )


def get_any_score(db: Session, contract_id: str, month: str) -> MonthlyScore | None:
    """优先返回已锁定的评分（跨模式）；否则返回任一已有评分。"""
    rows = (
        db.query(MonthlyScore)
        .filter_by(contract_id=contract_id, month=month)
        .order_by(MonthlyScore.id)
        .all()
    )
    for r in rows:
        if r.status == "locked":
            return r
    return rows[0] if rows else None


def _latest_version_row(db: Session, score: MonthlyScore) -> ScoreVersion | None:
    return (
        db.query(ScoreVersion)
        .filter_by(score_id=score.id)
        .order_by(ScoreVersion.version.desc())
        .first()
    )


def _task_deduction_map(version_row: ScoreVersion | None) -> dict:
    if version_row is None:
        return {}
    return {d["task_id"]: d.get("deduction", 0.0) for d in version_row.snapshot.get("details", [])}


# ---------------------------------------------------------------- 评分运行


def run_scoring(
    db: Session,
    contract: Contract,
    month: str,
    *,
    mode: str,
    username: str,
    change_note: str = "",
) -> MonthlyScore:
    if mode not in ("incremental", "month_end"):
        raise HTTPException(409, "评分模式只能是 incremental 或 month_end")
    if not contract.confirmed:
        raise HTTPException(409, f"合同 {contract.id} 尚未确认，不能形成评分草稿")

    score = get_score(db, contract.id, month, mode)
    if score is not None and score.status == "locked":
        raise HTTPException(409, "月结已锁定，评分不能改写；迟到证据/追溯裁定请走调整单")

    tasks = sla_adherence.eligible_tasks(db, contract.id, month)
    details, risks = [], []
    total = 0.0
    for task in tasks:
        snap = sla_adherence.evaluate_task(db, task)
        details.append(snap)
        total = round(total + snap["deduction"], 2)
        risks.extend(snap["risks"])

    # 被排除任务（合同未确认等）作为风险可见，不进入扣分
    start, end = sla_adherence.month_bounds(month)
    excluded = (
        db.query(ScheduledTask)
        .filter(
            ScheduledTask.contract_id == contract.id,
            ScheduledTask.status == "excluded",
            ScheduledTask.scheduled_time >= start,
            ScheduledTask.scheduled_time < end,
        )
        .all()
    )
    for t in excluded:
        risks.append({"type": "task_excluded", "message": f"任务 {t.id} 未进入评分：{t.exclude_reason}"})

    # 未关联任务的未核投诉：同样只列风险
    orphan_complaints = (
        db.query(Complaint)
        .filter(Complaint.status == "unverified", Complaint.task_id.is_(None))
        .all()
    )
    for c in orphan_complaints:
        risks.append(
            {
                "type": "complaint_unverified",
                "message": f"投诉 #{c.id}（{c.address}）尚未核实，暂不扣分",
                "complaint_id": c.id,
            }
        )

    new_map = {d["task_id"]: d["deduction"] for d in details}
    if score is None:
        score = MonthlyScore(
            contract_id=contract.id,
            month=month,
            mode=mode,
            version=0,
            status="draft",
            base_score=100.0,
        )
        db.add(score)
        db.flush()

    prev_row = _latest_version_row(db, score)
    prev_map = _task_deduction_map(prev_row)
    if prev_row is None:
        # 首次评分：本版本纳入的全部车次即为受影响车次
        affected = sorted(new_map.keys())
    else:
        affected = sorted(
            {
                tid
                for tid in set(new_map) | set(prev_map)
                if new_map.get(tid, 0.0) != prev_map.get(tid, 0.0)
            }
        )
    new_score_value = round(max(0.0, score.base_score - total), 2)

    # 与上一版本完全一致且非首次运行：不制造空版本
    if prev_row is not None and not affected and prev_row.score == new_score_value:
        score.review_required = False
        score.review_reason = ""
        db.commit()
        db.refresh(score)
        return score

    version = (prev_row.version + 1) if prev_row else 1
    note = change_note or (
        f"{'首次评分' if version == 1 else '证据到达触发重算'}，受影响车次 {len(affected)} 个"
    )
    row = ScoreVersion(
        score_id=score.id,
        version=version,
        score=new_score_value,
        total_deduction=total,
        affected_task_ids=affected,
        change_note=note,
        snapshot={"details": details, "risks": risks},
    )
    db.add(row)
    score.version = version
    score.score = new_score_value
    score.total_deduction = total
    score.affected_task_ids = affected
    score.details = details
    score.risks = risks
    # 主管重新查看影响车次后重算：清除并发标记
    score.review_required = False
    score.review_reason = ""
    db.commit()
    db.refresh(score)
    return score


# ---------------------------------------------------------------- 发布与锁定


def publish_score(db: Session, score: MonthlyScore, username: str) -> MonthlyScore:
    if score.status == "locked":
        raise HTTPException(409, "月结已锁定")
    if score.version == 0:
        raise HTTPException(409, "尚无评分版本，请先运行评分")
    if score.review_required:
        raise HTTPException(409, "存在申辩裁定与评分并发的待复查事项，请先重新查看影响车次并重算")
    score.status = "published"
    db.commit()
    db.refresh(score)
    return score


def lock_score(db: Session, score: MonthlyScore, username: str, evidence_batch_ref: str) -> MonthlyScore:
    if score.version == 0:
        raise HTTPException(409, "尚无评分结果，不能锁定")
    pending_appeals = _pending_appeal_count(db, score)
    if pending_appeals:
        raise HTTPException(409, f"仍有 {pending_appeals} 条承运商申辩待主管裁定，不能月结锁定")
    score.status = "locked"
    score.locked_at = datetime.now(timezone.utc)
    score.locked_by = username
    score.evidence_batch_ref = evidence_batch_ref or f"BATCH-{score.month}-{score.contract_id}"
    db.commit()
    db.refresh(score)
    return score


def mark_paid(db: Session, score: MonthlyScore) -> MonthlyScore:
    if score.status != "locked":
        raise HTTPException(409, "只有月结锁定的评分才能登记付款")
    score.payment_status = "paid"
    score.paid_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(score)
    return score


def _pending_appeal_count(db: Session, score: MonthlyScore) -> int:
    from ..models import CarrierAppeal

    start, end = sla_adherence.month_bounds(score.month)
    task_ids = [
        t.id
        for t in db.query(ScheduledTask)
        .filter(
            ScheduledTask.contract_id == score.contract_id,
            ScheduledTask.scheduled_time >= start,
            ScheduledTask.scheduled_time < end,
        )
        .all()
    ]
    if not task_ids:
        return 0
    return db.query(CarrierAppeal).filter(
        CarrierAppeal.task_id.in_(task_ids), CarrierAppeal.status == "pending"
    ).count()


# ---------------------------------------------------------------- 并发：裁定影响


def on_ruling_change(db: Session, appeal, ruling) -> LockAdjustment | None:
    """申辩结论落地时判断对既有评分的影响。返回调整单（锁定后）或 None（置复查标记）。"""
    task = db.get(ScheduledTask, appeal.task_id)
    month = _month_of(task.scheduled_time)
    score = get_any_score(db, task.contract_id, month)
    if score is None or score.version == 0:
        return None  # 尚未评分，下次运行自然纳入

    if score.status == "locked" or score.payment_status == "paid":
        prev_row = _latest_version_row(db, score)
        old_deduction = _task_deduction_map(prev_row).get(task.id, 0.0)
        new_snap = sla_adherence.evaluate_task(db, task)
        delta = round(new_snap["deduction"] - old_deduction, 2)
        adj = LockAdjustment(
            score_id=score.id,
            task_id=task.id,
            adjustment_type="ruling_after_lock",
            delta_deduction=delta,
            reason=(
                f"月结{'已支付' if score.payment_status == 'paid' else '已锁定'}后主管裁定 "
                f"{ruling.outcome}（申辩 #{appeal.id}，违约类型 {appeal.violation_type}）；"
                "原凭证不改写，差额走调整记录"
            ),
            evidence_ref=f"ruling-{ruling.id}",
            created_by=ruling.ruled_by,
        )
        db.add(adj)
        db.flush()
        return adj

    # 未锁定：申辩结论与评分发布并发 → 标记主管需重新查看影响车次
    score.review_required = True
    score.review_reason = (
        f"申辩 #{appeal.id} 裁定 {ruling.outcome} 影响车次 {task.id}，"
        "主管需重新查看影响车次后重算评分"
    )
    return None


# ---------------------------------------------------------------- 锁定后迟到证据 → 调整单


def register_locked_evidence(db: Session, task: ScheduledTask, kind: str, **kw) -> LockAdjustment | None:
    """锁定后到达的签收/核实投诉登记为调整单（open），不改动锁定分数与原付款。"""
    from datetime import timedelta

    month = _month_of(task.scheduled_time)
    score = get_any_score(db, task.contract_id, month)
    if score is None or score.status != "locked":
        return None

    contract = db.get(Contract, task.contract_id)
    pen = sla_adherence.penalties(contract)
    if kind == "late_signoff":
        signoff: FacilitySignoff = kw["signoff"]
        signoff.adjusted = True
        due_hours = int((contract.sla_rules or {}).get("receipt_due_hours", 24))
        due = task.scheduled_time + timedelta(hours=due_hours)
        delay_hours = round((signoff.signoff_time - due).total_seconds() / 3600.0, 2)
        is_late = delay_hours > 0
        adj_type = "late_signoff_after_lock"
        delta = pen["late_receipt"] if is_late else 0.0
        reason = (
            f"锁定后{'迟到' if is_late else '及时'}签收（签收事件 #{signoff.id}，"
            f"相对 {due_hours} 小时时限 {delay_hours} 小时），旧付款不改写，另出调整单"
        )
        ref = f"signoff-{signoff.id}"
    elif kind == "complaint_verified":
        complaint: Complaint = kw["complaint"]
        adj_type = "complaint_verified_after_lock"
        delta = pen["valid_complaint"]
        reason = f"锁定后投诉 #{complaint.id} 核实成立并关联任务 {task.id}，追加调整"
        ref = f"complaint-{complaint.id}"
    else:
        return None

    adj = LockAdjustment(
        score_id=score.id,
        task_id=task.id,
        adjustment_type=adj_type,
        delta_deduction=delta,
        reason=reason,
        evidence_ref=ref,
        created_by=kw.get("username", ""),
    )
    db.add(adj)
    db.flush()
    return adj


def apply_adjustment(db: Session, adj: LockAdjustment, username: str) -> LockAdjustment:
    if adj.status == "applied":
        raise HTTPException(409, "调整单已处理")
    score = db.get(MonthlyScore, adj.score_id)
    if score.status != "locked":
        raise HTTPException(409, "只能对月结锁定的评分应用调整单")
    adj.status = "applied"
    adj.applied_at = datetime.now(timezone.utc)
    adj.created_by = adj.created_by or username
    db.commit()
    db.refresh(adj)
    return adj


# ---------------------------------------------------------------- 月内进度与缺口（月结模式）


def month_progress(db: Session, contract: Contract, month: str) -> dict:
    tasks = sla_adherence.eligible_tasks(db, contract.id, month)
    total = len(tasks)
    arrival_ok = signoff_ok = 0
    gaps = []
    for t in tasks:
        snap = sla_adherence.evaluate_task(db, t)
        if snap["arrival"] is not None:
            arrival_ok += 1
        else:
            gaps.append({"task_id": t.id, "gap": "missing_arrival", "message": "缺少车辆到场证据（疑似漏收）"})
        if snap["signoff"] is not None:
            signoff_ok += 1
        else:
            gaps.append({"task_id": t.id, "gap": "missing_signoff", "message": "缺少处理场签收证据"})
    unverified = db.query(Complaint).filter(Complaint.status == "unverified").count()
    return {
        "contract_id": contract.id,
        "month": month,
        "mode": "month_end",
        "total_tasks": total,
        "arrival_covered": arrival_ok,
        "signoff_covered": signoff_ok,
        "unverified_complaints": unverified,
        "evidence_complete_pct": round(((arrival_ok + signoff_ok) / (total * 2)) * 100, 1) if total else 0.0,
        "gaps": gaps,
    }


# ---------------------------------------------------------------- 序列化


def version_to_dict(row: ScoreVersion) -> dict:
    return {
        "id": row.id,
        "version": row.version,
        "score": row.score,
        "total_deduction": row.total_deduction,
        "affected_task_ids": row.affected_task_ids,
        "change_note": row.change_note,
        "created_at": row.created_at.isoformat(),
    }


def score_to_dict(db: Session, score: MonthlyScore) -> dict:
    rows = (
        db.query(ScoreVersion)
        .filter_by(score_id=score.id)
        .order_by(ScoreVersion.version)
        .all()
    )
    adjustments = db.query(LockAdjustment).filter_by(score_id=score.id).order_by(LockAdjustment.id).all()
    open_adj = sum(1 for a in adjustments if a.status == "open")
    applied_delta = round(sum(a.delta_deduction for a in adjustments if a.status == "applied"), 2)
    return {
        "id": score.id,
        "contract_id": score.contract_id,
        "month": score.month,
        "mode": score.mode,
        "version": score.version,
        "status": score.status,
        "base_score": score.base_score,
        "score": score.score,
        "total_deduction": score.total_deduction,
        "affected_task_ids": score.affected_task_ids,
        "details": score.details,
        "risks": score.risks,
        "evidence_batch_ref": score.evidence_batch_ref,
        "locked_at": score.locked_at.isoformat() if score.locked_at else None,
        "locked_by": score.locked_by,
        "payment_status": score.payment_status,
        "paid_at": score.paid_at.isoformat() if score.paid_at else None,
        "review_required": score.review_required,
        "review_reason": score.review_reason,
        "versions": [version_to_dict(r) for r in rows],
        "adjustments": [
            {
                "id": a.id,
                "task_id": a.task_id,
                "adjustment_type": a.adjustment_type,
                "delta_deduction": a.delta_deduction,
                "reason": a.reason,
                "evidence_ref": a.evidence_ref,
                "status": a.status,
                "created_at": a.created_at.isoformat(),
                "applied_at": a.applied_at.isoformat() if a.applied_at else None,
            }
            for a in adjustments
        ],
        "open_adjustment_count": open_adj,
        "applied_adjustment_delta": applied_delta,
        "adjusted_score": round(max(0.0, score.score - applied_delta), 2),
    }
