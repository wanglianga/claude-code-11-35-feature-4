"""SLA 绩效域 API：合同确认、任务证据、投诉合并、申辩裁定、月度评分、月结调整。

权限：
- analyst（数据团队）：证据录入、评分草稿运行/发布；
- supervisor（物业主管）：合同确认、投诉核实、合并与申辩裁定、月结锁定、付款登记与调整应用；
- auditor（财务只读）：全部 GET 可读，写操作 403。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import (
    CarrierAppeal,
    Complaint,
    ComplaintMergeGroup,
    Contract,
    FacilitySignoff,
    LockAdjustment,
    MonthlyScore,
    ScheduledTask,
    SupervisorRuling,
    VehicleArrival,
    User,
)
from ..schemas import (
    AppealIn,
    ComplaintIn,
    ComplaintVerifyIn,
    ContractConfirmIn,
    FacilitySignoffIn,
    MergeDecisionIn,
    RulingIn,
    ScheduledTaskIn,
    ScoreLockIn,
    ScoreRefIn,
    ScoringRunIn,
    VehicleArrivalIn,
)
from ..security import get_current_user, require_analyst, require_supervisor
from ..services import sla_adherence, sla_appeals, sla_complaints, sla_evidence, sla_scoring

router = APIRouter(prefix="/api/sla", tags=["sla-performance"])


# ---------------------------------------------------------------- 合同确认


@router.post("/contracts/{contract_id}/confirm")
def confirm_contract(
    contract_id: str,
    body: ContractConfirmIn,
    db: OrmSession = Depends(get_db),
    user: User = Depends(require_supervisor),
):
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise HTTPException(404, "合同不存在")
    contract.confirmed = True
    contract.confirmed_at = datetime.now(timezone.utc)
    contract.sla_rules = body.sla_rules.model_dump()
    # 合同确认后，窗口内既有任务恢复评分资格
    for t in db.query(ScheduledTask).filter_by(contract_id=contract_id, status="excluded").all():
        t.status = "eligible"
        t.exclude_reason = ""
    db.commit()
    db.refresh(contract)
    return {"id": contract.id, "confirmed": contract.confirmed, "sla_rules": contract.sla_rules}


# ---------------------------------------------------------------- 排班/到场/签收


def _task_dict(db: OrmSession, t: ScheduledTask) -> dict:
    return {
        "id": t.id,
        "contract_id": t.contract_id,
        "vehicle_id": t.vehicle_id,
        "address": t.address,
        "latitude": t.latitude,
        "longitude": t.longitude,
        "scheduled_time": t.scheduled_time.isoformat(),
        "status": t.status,
        "exclude_reason": t.exclude_reason,
        "compliance": t.compliance,
    }


@router.post("/tasks", status_code=201)
def create_task(
    body: ScheduledTaskIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    if db.get(Contract, body.contract_id) is None:
        raise HTTPException(404, "合同不存在")
    task = sla_evidence.create_task(db, **body.model_dump())
    return _task_dict(db, task)


@router.get("/tasks")
def list_tasks(
    contract_id: str | None = None,
    month: str | None = None,
    status: str | None = None,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(ScheduledTask)
    if contract_id:
        q = q.filter_by(contract_id=contract_id)
    if status:
        q = q.filter_by(status=status)
    tasks = q.order_by(ScheduledTask.scheduled_time).all()
    if month:
        try:
            start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(400, "month 格式应为 YYYY-MM")
        end = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=timezone.utc)
        tasks = [t for t in tasks if start <= t.scheduled_time < end]
    return [_task_dict(db, t) for t in tasks]


@router.get("/tasks/{task_id}")
def task_detail(
    task_id: str,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """评分下钻：单个受影响车次的合同条款、任务证据与裁决记录一站回查。"""
    task = db.get(ScheduledTask, task_id)
    if task is None:
        raise HTTPException(404, "排班任务不存在")
    arrivals = db.query(VehicleArrival).filter_by(task_id=task_id).order_by(VehicleArrival.arrival_time).all()
    signoffs = db.query(FacilitySignoff).filter_by(task_id=task_id).order_by(FacilitySignoff.signoff_time).all()
    complaints = db.query(Complaint).filter_by(task_id=task_id).all()
    appeals = db.query(CarrierAppeal).filter_by(task_id=task_id).order_by(CarrierAppeal.id).all()
    contract = db.get(Contract, task.contract_id)
    snap = sla_adherence.evaluate_task(db, task)
    return {
        "task": _task_dict(db, task),
        "contract_clause": {
            "contract_id": contract.id,
            "confirmed": contract.confirmed,
            "sla_rules": contract.sla_rules,
        },
        "arrivals": [
            {
                "id": a.id,
                "arrival_time": a.arrival_time.isoformat(),
                "source_ref": a.source_ref,
                "raw_text": a.raw_text,
                "content_hash": a.content_hash,
            }
            for a in arrivals
        ],
        "signoffs": [
            {
                "id": s.id,
                "signoff_time": s.signoff_time.isoformat(),
                "site_id": s.site_id,
                "source_ref": s.source_ref,
                "weight_tons": s.weight_tons,
                "adjusted": s.adjusted,
                "content_hash": s.content_hash,
            }
            for s in signoffs
        ],
        "complaints": [sla_complaints.complaint_to_dict(c) for c in complaints],
        "appeals": [
            sla_appeals.appeal_to_dict(a, db.query(SupervisorRuling).filter_by(appeal_id=a.id).first())
            for a in appeals
        ],
        "compliance_snapshot": snap,
    }


@router.post("/arrivals", status_code=201)
def create_arrival(
    body: VehicleArrivalIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    arrival = sla_evidence.create_arrival(db, **body.model_dump())
    return {
        "id": arrival.id,
        "vehicle_id": arrival.vehicle_id,
        "address": arrival.address,
        "arrival_time": arrival.arrival_time.isoformat(),
        "task_id": arrival.task_id,
        "content_hash": arrival.content_hash,
    }


@router.get("/arrivals")
def list_arrivals(
    task_id: str | None = None,
    vehicle_id: str | None = None,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(VehicleArrival)
    if task_id:
        q = q.filter_by(task_id=task_id)
    if vehicle_id:
        q = q.filter_by(vehicle_id=vehicle_id)
    return [
        {
            "id": a.id,
            "vehicle_id": a.vehicle_id,
            "address": a.address,
            "arrival_time": a.arrival_time.isoformat(),
            "source_ref": a.source_ref,
            "task_id": a.task_id,
        }
        for a in q.order_by(VehicleArrival.arrival_time).limit(500).all()
    ]


@router.post("/signoffs", status_code=201)
def create_signoff(
    body: FacilitySignoffIn,
    db: OrmSession = Depends(get_db),
    user: User = Depends(require_analyst),
):
    signoff = sla_evidence.create_signoff(db, **body.model_dump())
    task = db.get(ScheduledTask, body.task_id)
    # 月结锁定后到达的签收：不改原凭证，登记调整单（服务层按合同时限判定迟到与差额）
    adj = sla_scoring.register_locked_evidence(
        db, task, "late_signoff", signoff=signoff, username=user.username
    )
    if adj is not None:
        db.commit()
        db.refresh(adj)
    return {
        "id": signoff.id,
        "task_id": signoff.task_id,
        "signoff_time": signoff.signoff_time.isoformat(),
        "adjusted": signoff.adjusted,
        "lock_adjustment_id": adj.id if adj else None,
        "content_hash": signoff.content_hash,
    }


@router.get("/signoffs")
def list_signoffs(
    task_id: str | None = None,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(FacilitySignoff)
    if task_id:
        q = q.filter_by(task_id=task_id)
    return [
        {
            "id": s.id,
            "task_id": s.task_id,
            "vehicle_id": s.vehicle_id,
            "signoff_time": s.signoff_time.isoformat(),
            "site_id": s.site_id,
            "weight_tons": s.weight_tons,
            "adjusted": s.adjusted,
        }
        for s in q.order_by(FacilitySignoff.signoff_time).limit(500).all()
    ]


# ---------------------------------------------------------------- 投诉与合并


@router.post("/complaints", status_code=201)
def create_complaint(
    body: ComplaintIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    complaint = sla_evidence.create_complaint(db, **body.model_dump())
    return sla_complaints.complaint_to_dict(complaint)


@router.get("/complaints")
def list_complaints(
    status: str | None = None,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(Complaint)
    if status:
        q = q.filter_by(status=status)
    complaints = q.order_by(Complaint.event_time.desc()).limit(500).all()
    return [sla_complaints.complaint_to_dict(c) for c in complaints]


@router.post("/complaints/detect-merges", status_code=201)
def detect_merges(
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    groups = sla_complaints.detect_merges(db)
    return {"created_groups": [sla_complaints.group_to_dict(g) for g in groups]}


@router.get("/merge-groups")
def list_merge_groups(
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    groups = db.query(ComplaintMergeGroup).order_by(ComplaintMergeGroup.id).all()
    out = []
    for g in groups:
        members = db.query(Complaint).filter_by(merge_group_id=g.id).all()
        out.append(
            {
                **sla_complaints.group_to_dict(g),
                "members": [sla_complaints.complaint_to_dict(c) for c in members],
            }
        )
    return out


@router.post("/merge-groups/{group_id}/decision")
def decide_merge(
    group_id: int,
    body: MergeDecisionIn,
    db: OrmSession = Depends(get_db),
    user: User = Depends(require_supervisor),
):
    group = db.get(ComplaintMergeGroup, group_id)
    if group is None:
        raise HTTPException(404, "合并组不存在")
    return sla_complaints.group_to_dict(
        sla_complaints.decide_group(db, group, confirm=body.confirm, reason=body.reason, username=user.username)
    )


@router.post("/complaints/{complaint_id}/verify")
def verify_complaint(
    complaint_id: int,
    body: ComplaintVerifyIn,
    db: OrmSession = Depends(get_db),
    user: User = Depends(require_supervisor),
):
    complaint = db.get(Complaint, complaint_id)
    if complaint is None:
        raise HTTPException(404, "投诉不存在")
    if body.dismiss:
        complaint = sla_complaints.dismiss_complaint(db, complaint, note=body.note, username=user.username)
        return sla_complaints.complaint_to_dict(complaint)

    complaint = sla_complaints.verify_complaint(
        db, complaint, task_id=body.task_id, note=body.note, username=user.username
    )
    adj = None
    if body.task_id:
        task = db.get(ScheduledTask, body.task_id)
        adj = sla_scoring.register_locked_evidence(
            db, task, "complaint_verified", complaint=complaint, username=user.username
        )
        if adj is not None:
            db.commit()
            db.refresh(adj)
    return {**sla_complaints.complaint_to_dict(complaint), "lock_adjustment_id": adj.id if adj else None}


# ---------------------------------------------------------------- 申辩与裁定


@router.post("/appeals", status_code=201)
def create_appeal(
    body: AppealIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    task = db.get(ScheduledTask, body.task_id)
    if task is None:
        raise HTTPException(404, "排班任务不存在")
    appeal = sla_appeals.create_appeal(db, task, **body.model_dump())
    ruling = db.query(SupervisorRuling).filter_by(appeal_id=appeal.id).first()
    return sla_appeals.appeal_to_dict(appeal, ruling)


@router.get("/appeals")
def list_appeals(
    task_id: str | None = None,
    status_filter: str | None = None,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(CarrierAppeal)
    if task_id:
        q = q.filter_by(task_id=task_id)
    if status_filter:
        q = q.filter_by(status=status_filter)
    return [
        sla_appeals.appeal_to_dict(a, db.query(SupervisorRuling).filter_by(appeal_id=a.id).first())
        for a in q.order_by(CarrierAppeal.id).all()
    ]


@router.post("/appeals/{appeal_id}/rule")
def rule_appeal(
    appeal_id: int,
    body: RulingIn,
    db: OrmSession = Depends(get_db),
    user: User = Depends(require_supervisor),
):
    appeal = db.get(CarrierAppeal, appeal_id)
    if appeal is None:
        raise HTTPException(404, "申辩不存在")
    ruling = sla_appeals.rule_appeal(
        db, appeal, outcome=body.outcome, ruling_note=body.ruling_note, username=user.username
    )
    # 回看并发影响：未锁定 → review_required；锁定/已支付 → 调整单
    adj = (
        db.query(LockAdjustment)
        .filter(LockAdjustment.evidence_ref == f"ruling-{ruling.id}")
        .first()
    )
    return {
        **sla_appeals.appeal_to_dict(appeal, ruling),
        "lock_adjustment_id": adj.id if adj else None,
        "review_required": _score_review_flag(db, appeal),
    }


def _score_review_flag(db: OrmSession, appeal: CarrierAppeal) -> bool:
    task = db.get(ScheduledTask, appeal.task_id)
    score = sla_scoring.get_any_score(db, task.contract_id, task.scheduled_time.strftime("%Y-%m"))
    return bool(score and score.review_required)


# ---------------------------------------------------------------- 月度评分


def _require_score(db: OrmSession, contract_id: str, month: str, mode: str) -> MonthlyScore:
    score = sla_scoring.get_score(db, contract_id, month, mode)
    if score is None:
        raise HTTPException(404, "该合同/月份/模式尚无评分，请先运行评分")
    return score


def _get_contract(db: OrmSession, contract_id: str) -> Contract:
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise HTTPException(404, "合同不存在")
    return contract


@router.post("/scoring/run")
def run_scoring(
    body: ScoringRunIn,
    db: OrmSession = Depends(get_db),
    user: User = Depends(require_analyst),
):
    contract = _get_contract(db, body.contract_id)
    score = sla_scoring.run_scoring(
        db,
        contract,
        body.month,
        mode=body.mode,
        username=user.username,
        change_note=body.change_note,
    )
    return sla_scoring.score_to_dict(db, score)


@router.post("/scoring/publish")
def publish_scoring(
    body: ScoreRefIn,
    db: OrmSession = Depends(get_db),
    user: User = Depends(require_analyst),
):
    score = _require_score(db, body.contract_id, body.month, body.mode)
    return sla_scoring.score_to_dict(db, sla_scoring.publish_score(db, score, user.username))


@router.post("/scoring/lock")
def lock_scoring(
    body: ScoreLockIn,
    db: OrmSession = Depends(get_db),
    user: User = Depends(require_supervisor),
):
    score = _require_score(db, body.contract_id, body.month, body.mode)
    return sla_scoring.score_to_dict(
        db, sla_scoring.lock_score(db, score, user.username, body.evidence_batch_ref)
    )


@router.post("/scoring/mark-paid")
def mark_paid(
    body: ScoreRefIn,
    db: OrmSession = Depends(get_db),
    user: User = Depends(require_supervisor),
):
    score = _require_score(db, body.contract_id, body.month, body.mode)
    return sla_scoring.score_to_dict(db, sla_scoring.mark_paid(db, score))


@router.get("/scoring")
def get_scoring(
    contract_id: str,
    month: str,
    mode: str,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """评分下钻：版本流水、受影响车次、任务明细、风险、调整单一并返回。"""
    score = _require_score(db, contract_id, month, mode)
    return sla_scoring.score_to_dict(db, score)


@router.get("/scoring/list")
def list_scoring(
    month: str | None = None,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(MonthlyScore)
    if month:
        q = q.filter_by(month=month)
    scores = q.order_by(MonthlyScore.month, MonthlyScore.contract_id, MonthlyScore.mode).all()
    return [sla_scoring.score_to_dict(db, s) for s in scores]


@router.get("/progress")
def month_progress(
    contract_id: str,
    month: str,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """月结模式的月内进度与证据缺口（月内不显示稳定分数，只显示进度和缺口）。"""
    contract = _get_contract(db, contract_id)
    return sla_scoring.month_progress(db, contract, month)


@router.get("/adjustments")
def list_adjustments(
    contract_id: str | None = None,
    month: str | None = None,
    db: OrmSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(LockAdjustment)
    rows = q.order_by(LockAdjustment.id).all()
    out = []
    for adj in rows:
        score = db.get(MonthlyScore, adj.score_id)
        if contract_id and score.contract_id != contract_id:
            continue
        if month and score.month != month:
            continue
        out.append(
            {
                "id": adj.id,
                "score_id": adj.score_id,
                "contract_id": score.contract_id,
                "month": score.month,
                "task_id": adj.task_id,
                "adjustment_type": adj.adjustment_type,
                "delta_deduction": adj.delta_deduction,
                "reason": adj.reason,
                "evidence_ref": adj.evidence_ref,
                "status": adj.status,
                "created_at": adj.created_at.isoformat(),
                "applied_at": adj.applied_at.isoformat() if adj.applied_at else None,
            }
        )
    return out


@router.post("/adjustments/{adjustment_id}/apply")
def apply_adjustment(
    adjustment_id: int,
    db: OrmSession = Depends(get_db),
    user: User = Depends(require_supervisor),
):
    adj = db.get(LockAdjustment, adjustment_id)
    if adj is None:
        raise HTTPException(404, "调整单不存在")
    return {
        "id": adj.id,
        "status": sla_scoring.apply_adjustment(db, adj, user.username).status,
        "applied_at": adj.applied_at.isoformat() if adj.applied_at else None,
    }
