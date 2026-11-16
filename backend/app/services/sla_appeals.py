"""承运商申辩与主管裁定：并列保存；申辩只改变对应任务的裁定状态。

并发处理：
- 评分已发布但未锁定时出现裁定结论 → 评分置 review_required，主管需重新查看影响车次后重算；
- 评分已月结锁定（或已支付）后出现追溯变更 → 不改原凭证，只产生调整单。
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (
    CarrierAppeal,
    MonthlyScore,
    ScheduledTask,
    SupervisorRuling,
)


def create_appeal(
    db: Session,
    task: ScheduledTask,
    *,
    violation_type: str,
    reason: str,
    carrier_ref: str = "",
    evidence_ref: str = "",
) -> CarrierAppeal:
    appeal = CarrierAppeal(
        task_id=task.id,
        carrier_ref=carrier_ref,
        violation_type=violation_type,
        reason=reason,
        evidence_ref=evidence_ref,
        status="pending",
    )
    db.add(appeal)
    db.commit()
    db.refresh(appeal)
    return appeal


def rule_appeal(
    db: Session,
    appeal: CarrierAppeal,
    *,
    outcome: str,
    ruling_note: str,
    username: str,
) -> SupervisorRuling:
    if appeal.status != "pending":
        raise HTTPException(409, "该申辩已有主管裁定，不能重复裁定")
    if outcome not in ("upheld", "overturned"):
        raise HTTPException(409, "裁定结论只能是 upheld（维持违约）或 overturned（采纳申辩）")

    ruling = SupervisorRuling(
        appeal_id=appeal.id,
        task_id=appeal.task_id,
        outcome=outcome,
        ruling_note=ruling_note,
        ruled_by=username,
    )
    appeal.status = outcome
    appeal.decided_at = datetime.now(timezone.utc)
    db.add(ruling)
    db.flush()

    # 延迟导入避免循环依赖
    from . import sla_scoring

    sla_scoring.on_ruling_change(db, appeal, ruling)
    db.commit()
    db.refresh(ruling)
    return ruling


def appeal_to_dict(a: CarrierAppeal, ruling: SupervisorRuling | None = None) -> dict:
    return {
        "id": a.id,
        "task_id": a.task_id,
        "carrier_ref": a.carrier_ref,
        "violation_type": a.violation_type,
        "reason": a.reason,
        "evidence_ref": a.evidence_ref,
        "status": a.status,
        "created_at": a.created_at.isoformat(),
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "ruling": (
            {
                "id": ruling.id,
                "outcome": ruling.outcome,
                "ruling_note": ruling.ruling_note,
                "ruled_by": ruling.ruled_by,
                "created_at": ruling.created_at.isoformat(),
            }
            if ruling
            else None
        ),
    }
