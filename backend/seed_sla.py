"""SLA 绩效域演示种子（2026-11 服务月）。幂等：已存在 HT-2026-SLA-1 时跳过。

覆盖验收要点：
- 仅已确认合同的任务进入评分；未确认合同任务被排除并列风险。
- 漏收 / 到场超时 / 迟到签收 / 核实投诉四类违约；未核投诉只列风险不扣分。
- 重复投诉按地址+时间窗形成合并候选，主管确认后整组只计一次罚分。
- 承运商申辩与主管裁定并列保存；overturned 撤销违约、upheld 维持。
- 增量模式多版本（每版记录受影响车次）；月结模式锁定后迟到签收/追溯裁定走调整单，
  旧付款不改写；已支付罚款的返还以负差额调整记录体现。
"""

from app.db import SessionLocal
from app.models import Contract, ScheduledTask, User
from app.security import make_password_hash
from app.services import (
    sla_appeals,
    sla_complaints,
    sla_evidence,
    sla_scoring,
)

MONTH = "2026-11"
SLA_CONTRACT = "HT-2026-SLA-1"
UNCONFIRMED_CONTRACT = "HT-2026-SLA-X"

SLA_RULES = {
    "on_time_arrival_minutes": 10,
    "receipt_due_hours": 24,
    "complaint_merge_window_minutes": 120,
    "complaint_radius_meters": 300,
    "penalties": {
        "missed_collection": 8.0,
        "late_arrival": 3.0,
        "late_receipt": 2.0,
        "valid_complaint": 5.0,
    },
}


def seed_sla():
    db = SessionLocal()
    try:
        if db.get(Contract, SLA_CONTRACT):
            return

        # ---------------- 主管账号 ----------------
        if db.query(User).filter_by(username="supervisor").first() is None:
            db.add(
                User(
                    username="supervisor",
                    password_hash=make_password_hash("Supervisor@2026"),
                    role="supervisor",
                    display_name="物业主管·裁定员",
                )
            )
            db.commit()

        # ---------------- 合同：一个已确认、一个未确认 ----------------
        db.add_all(
            [
                Contract(
                    id=SLA_CONTRACT,
                    name="城东片区生活垃圾收运服务水平合同（SLA）",
                    contract_type="服务水平合同",
                    strategy="priority",
                    source_priority=["facility_receipt", "weighbridge_reading", "gate_record"],
                    confirmed=True,
                    sla_rules=SLA_RULES,
                ),
                Contract(
                    id=UNCONFIRMED_CONTRACT,
                    name="待签署新城收运合同（尚未确认，不进入评分）",
                    contract_type="服务水平合同",
                    strategy="priority",
                    source_priority=["facility_receipt"],
                    confirmed=False,
                    sla_rules=SLA_RULES,
                ),
            ]
        )
        db.commit()

        def task(tid, vehicle, addr, when, contract=SLA_CONTRACT):
            return sla_evidence.create_task(
                db,
                task_id=tid,
                contract_id=contract,
                vehicle_id=vehicle,
                address=addr,
                scheduled_time=dt(when),
            )

        def arrival(vehicle, addr, when, ref):
            return sla_evidence.create_arrival(
                db,
                vehicle_id=vehicle,
                address=addr,
                arrival_time=dt(when),
                source_ref=ref,
                raw_text=f"车载终端/门岗到场流水 {ref}",
            )

        def signoff(tid, vehicle, when, ref, weight=None):
            return sla_evidence.create_signoff(
                db,
                task_id=tid,
                vehicle_id=vehicle,
                signoff_time=dt(when),
                site_id="FAC-01",
                source_ref=ref,
                raw_text=f"处理场签收单 {ref}",
                weight_tons=weight,
            )

        def complaint(reporter, addr, when, category, desc):
            return sla_evidence.create_complaint(
                db,
                reporter=reporter,
                address=addr,
                event_time=dt(when),
                category=category,
                description=desc,
                raw_text=f"居民热线工单：{desc}",
            )

        # ---------------- 排班任务（分阶段创建，避免未来任务被早期评分误判漏收） ----------------
        # 阶段一仅 T1~T4 在场；被排除任务属于未确认合同，不参与评分可先建
        t1 = task("ST-20261103-01", "沪A-S201", "城东小区1号", "2026-11-03 08:00")
        t2 = task("ST-20261103-02", "沪A-S201", "城东小区2号", "2026-11-03 08:30")
        t3 = task("ST-20261105-01", "沪A-S202", "滨河路45号", "2026-11-05 09:00")
        t4 = task("ST-20261106-01", "沪A-S203", "园林街8号", "2026-11-06 07:30")
        task("ST-20261121-99", "沪A-S299", "新城待建点7号", "2026-11-21 09:00", contract=UNCONFIRMED_CONTRACT)

        # ================= 阶段一：T1~T4 证据 → 增量评分 v1（87 分） =================
        # T1 全程合规
        arrival("沪A-S201", "城东小区1号", "2026-11-03 08:05", "ARR-1103-01")
        signoff(t1.id, "沪A-S201", "2026-11-03 10:00", "SIG-1103-01", 11.20)
        # T2 漏收：无任何到场证据；一条未核投诉只列风险
        complaint("郑先生", "城东小区2号", "2026-11-03 09:10", "missed_collection", "全天未见收运车辆，垃圾滞留")
        # T3 到场超时 25 分钟（容差 10）
        arrival("沪A-S202", "滨河路45号", "2026-11-05 09:25", "ARR-1105-01")
        signoff(t3.id, "沪A-S202", "2026-11-05 11:00", "SIG-1105-01", 8.40)
        # T4 准时到场但迟到签收（时限 24h，实际超 1.5 小时）
        arrival("沪A-S203", "园林街8号", "2026-11-06 07:35", "ARR-1106-01")
        signoff(t4.id, "沪A-S203", "2026-11-07 09:00", "SIG-1106-01", 6.70)

        contract = db.get(Contract, SLA_CONTRACT)
        v1 = sla_scoring.run_scoring(db, contract, MONTH, mode="incremental", username="analyst")
        assert v1.score == 87.0, v1.score  # 漏收8 + 超时3 + 迟到签收2

        # ================= 阶段二：T5~T8 证据、合并投诉、申辩裁定 → v2（74 分） =================
        t5 = task("ST-20261108-01", "沪A-S202", "梧桐里12号", "2026-11-08 10:00")
        t6 = task("ST-20261110-01", "沪A-S204", "海棠巷3号", "2026-11-10 08:00")
        t7 = task("ST-20261112-01", "沪A-S201", "城东小区1号", "2026-11-12 08:00")
        t8 = task("ST-20261115-01", "沪A-S205", "青石路99号", "2026-11-15 09:30")
        t9 = task("ST-20261118-01", "沪A-S203", "月河湾22号", "2026-11-18 07:00")
        t10 = task("ST-20261120-01", "沪A-S204", "南屏苑5号", "2026-11-20 09:00")
        # T5 准时；两位居民同地址 50 分钟内重复投诉 → 合并候选 → 确认同一事件只计一次
        arrival("沪A-S202", "梧桐里12号", "2026-11-08 10:04", "ARR-1108-01")
        signoff(t5.id, "沪A-S202", "2026-11-08 11:30", "SIG-1108-01", 7.10)
        c5a = complaint("周阿姨", "梧桐里12号", "2026-11-08 10:30", "spillage", "收运后地面残留污水")
        c5b = complaint("吴先生", "梧桐里12号", "2026-11-08 11:20", "spillage", "同一地点污水异味未清理")
        groups = sla_complaints.detect_merges(db)
        assert len(groups) == 1
        sla_complaints.decide_group(
            db, groups[0], confirm=True, reason="现场核查为同一泄漏事件的多人报告", username="supervisor"
        )
        sla_complaints.verify_complaint(db, c5a, task_id=t5.id, note="核实成立，关联车次 ST-20261108-01", username="supervisor")
        sla_complaints.verify_complaint(db, c5b, task_id=t5.id, note="同组投诉，合并计一次", username="supervisor")

        # T6 到场超时，但承运商申辩被采纳（overturned）→ 撤销违约
        arrival("沪A-S204", "海棠巷3号", "2026-11-10 08:20", "ARR-1110-01")
        signoff(t6.id, "沪A-S204", "2026-11-10 10:00", "SIG-1110-01", 5.90)
        appeal6 = sla_appeals.create_appeal(
            db, t6, violation_type="late_arrival",
            reason="路段交通管制备案，到场延迟属免责情形", carrier_ref="APL-202611-006",
        )
        sla_appeals.rule_appeal(db, appeal6, outcome="overturned",
                                ruling_note="交管备案属实，采纳申辩，撤销超时违约", username="supervisor")

        # T7 漏收，申辩被驳回（upheld）→ 违约维持
        appeal7 = sla_appeals.create_appeal(
            db, t7, violation_type="missed_collection",
            reason="声称已到场但终端故障", carrier_ref="APL-202611-007",
        )
        sla_appeals.rule_appeal(db, appeal7, outcome="upheld",
                                ruling_note="无任何到场/定位证据，驳回申辩，漏收成立", username="supervisor")

        # T8 准时，投诉暂未核实（本阶段不计罚）
        arrival("沪A-S205", "青石路99号", "2026-11-15 09:34", "ARR-1115-01")
        signoff(t8.id, "沪A-S205", "2026-11-15 11:00", "SIG-1115-01", 9.30)

        # T9 仅有到场，签收证据月内缺口（月结前不构成违约，列风险）
        arrival("沪A-S203", "月河湾22号", "2026-11-18 07:06", "ARR-1118-01")
        # T10 全程合规；一条未核投诉月内只列风险
        arrival("沪A-S204", "南屏苑5号", "2026-11-20 09:05", "ARR-1120-01")
        signoff(t10.id, "沪A-S204", "2026-11-20 10:40", "SIG-1120-01", 8.10)
        c10 = complaint("冯女士", "南屏苑5号", "2026-11-20 12:00", "odor", "垃圾桶周边异味明显")
        # 无法关联任何任务的未核投诉（孤儿风险）
        complaint("匿名", "未知街道1号", "2026-11-21 08:00", "other", "热线转办，地址待核实")

        v2 = sla_scoring.run_scoring(db, contract, MONTH, mode="incremental", username="analyst")
        assert v2.score == 74.0, v2.score  # 13 + 投诉5 + 漏收8（T6 撤销不计）

        # ================= 阶段三：T8 投诉核实 → v3（69 分），受影响车次仅 T8 =================
        c8 = complaint("陈师傅", "青石路99号", "2026-11-15 13:00", "missed_collection", "午后桶满未补运")
        sla_complaints.verify_complaint(db, c8, task_id=t8.id, note="监控核实补运缺失", username="supervisor")
        v3 = sla_scoring.run_scoring(db, contract, MONTH, mode="incremental", username="analyst",
                                     change_note="投诉 #%d 核实关联 T8，增量重算" % c8.id)
        assert v3.score == 69.0 and v3.affected_task_ids == [t8.id], (v3.score, v3.affected_task_ids)

        # 增量评分发布；此后 T3 申辩结论并发到达 → 复核标记 → 主管重看后重算清除
        sla_scoring.publish_score(db, v3, "analyst")
        appeal3 = sla_appeals.create_appeal(
            db, t3, violation_type="late_arrival",
            reason="小区门禁登记延迟", carrier_ref="APL-202611-003",
        )
        sla_appeals.rule_appeal(db, appeal3, outcome="upheld",
                                ruling_note="门禁延迟不构成免责，维持超时", username="supervisor")
        db.refresh(v3)
        assert v3.review_required is True
        sla_scoring.run_scoring(db, contract, MONTH, mode="incremental", username="analyst",
                                change_note="主管重新查看影响车次，确认维持原判")
        db.refresh(v3)
        assert v3.review_required is False and v3.score == 69.0

        # ================= 月结模式：进度缺口 → 统一计算 → 锁定 → 付款 =================
        progress = sla_scoring.month_progress(db, contract, MONTH)
        assert progress["total_tasks"] == 10 and progress["signoff_covered"] == 7  # T2/T7/T9 无签收
        me = sla_scoring.run_scoring(db, contract, MONTH, mode="month_end", username="analyst",
                                     change_note="月结证据批次 BATCH-2026-11 到达，统一计算")
        assert me.score == 69.0
        sla_scoring.lock_score(db, me, "supervisor", "BATCH-2026-11-HT-SLA1")
        sla_scoring.mark_paid(db, me)

        # ---- 锁定/付款后追溯变更：一律调整单，原凭证不改写 ----
        # ① T9 迟到签收在锁定后到达 → 追加扣罚 2 分
        sig_late = signoff(t9.id, "沪A-S203", "2026-11-20 10:00", "SIG-1118-LATE", 7.40)
        adj1 = sla_scoring.register_locked_evidence(db, t9, "late_signoff", signoff=sig_late, username="analyst")
        assert adj1 is not None and adj1.delta_deduction == 2.0
        db.refresh(sig_late)
        assert sig_late.adjusted is True
        # ② T10 投诉在付款后核实 → 追加扣罚 5 分
        adj2 = sla_scoring.register_locked_evidence(
            db, t10, "complaint_verified",
            complaint=sla_complaints.verify_complaint(db, c10, task_id=t10.id, note="付款后核实成立",
                                                      username="supervisor"),
            username="supervisor",
        )
        assert adj2 is not None and adj2.delta_deduction == 5.0
        # ③ T2 漏收申辩在付款后被采纳 → 返还 8 分（负差额调整，不改原付款凭证）
        appeal2 = sla_appeals.create_appeal(
            db, t2, violation_type="missed_collection",
            reason="补交物业无人签收的放行记录", carrier_ref="APL-202611-002",
        )
        ruling2 = sla_appeals.rule_appeal(
            db, appeal2, outcome="overturned",
            ruling_note="放行记录证明车辆已到，撤销漏收；已扣罚款走调整返还", username="supervisor",
        )
        adj3 = (
            db.query(type(adj1))
            .filter_by(evidence_ref=f"ruling-{ruling2.id}")
            .first()
        )
        assert adj3 is not None and adj3.delta_deduction == -8.0
        for adj in (adj1, adj2, adj3):
            sla_scoring.apply_adjustment(db, adj, "supervisor")

        db.commit()
        print(
            "[seed-sla] SLA 演示数据：10 个合格任务+1 个排除任务；月结锁定分 69，"
            "调整单 3 笔（+2/+5/-8，净 -1），调整后 70 分；增量评分 3 个版本"
        )
    finally:
        db.close()


def dt(s: str):
    from datetime import datetime, timedelta, timezone

    cst = timezone(timedelta(hours=8))
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=cst)
