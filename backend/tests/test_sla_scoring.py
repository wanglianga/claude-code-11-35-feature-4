"""SLA 绩效域验收测试。

覆盖：评分资格、漏收/超时/迟到签收、未核投诉只列风险、重复投诉合并去重、
申辩裁定、增量版本与受影响车次、月结锁定、锁定后调整单（含已支付返还）、
并发复查标记、月内进度缺口、下钻载荷、内容哈希与角色权限。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import (
    CarrierAppeal,
    Contract,
    LockAdjustment,
    MonthlyScore,
    ScheduledTask,
    ScoreVersion,
    User,
)
from app.security import make_password_hash
from app.services import (
    sla_adherence,
    sla_appeals,
    sla_complaints,
    sla_evidence,
    sla_scoring,
)
from tests.conftest import dt, make_users

MONTH = "2026-11"
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


# ---------------------------------------------------------------- 构造助手


def make_sla_contract(db, cid="HT-S-001", confirmed=True):
    c = Contract(
        id=cid,
        name=f"SLA 测试合同 {cid}",
        contract_type="服务水平合同",
        strategy="priority",
        source_priority=["facility_receipt"],
        confirmed=confirmed,
        sla_rules=SLA_RULES,
    )
    db.add(c)
    db.commit()
    return c


def mktask(db, tid, cid="HT-S-001", when="2026-11-05 09:00", vehicle="沪A-T1", addr="测试路1号",
           lat=None, lon=None):
    return sla_evidence.create_task(
        db, task_id=tid, contract_id=cid, vehicle_id=vehicle, address=addr,
        scheduled_time=dt(when), latitude=lat, longitude=lon,
    )


def mkarrival(db, vehicle, addr, when, ref="ARR-1"):
    return sla_evidence.create_arrival(
        db, vehicle_id=vehicle, address=addr, arrival_time=dt(when),
        source_ref=ref, raw_text=f"到场流水 {ref}",
    )


def mksignoff(db, task, when, ref="SIG-1", vehicle="沪A-T1", weight=None):
    return sla_evidence.create_signoff(
        db, task_id=task.id, vehicle_id=vehicle, signoff_time=dt(when),
        site_id="FAC-01", source_ref=ref, raw_text=f"签收单 {ref}", weight_tons=weight,
    )


def mkcomplaint(db, reporter, addr, when, desc="投诉", lat=None, lon=None):
    return sla_evidence.create_complaint(
        db, reporter=reporter, address=addr, event_time=dt(when),
        category="missed_collection", description=desc, raw_text=f"工单：{desc}",
        latitude=lat, longitude=lon,
    )


def run(db, contract, mode="incremental", note=""):
    return sla_scoring.run_scoring(db, contract, MONTH, mode=mode, username="analyst", change_note=note)


# ---------------------------------------------------------------- ① 评分资格


def test_unconfirmed_contract_task_excluded_then_eligible(db):
    c = make_sla_contract(db, "HT-S-X1", confirmed=False)
    t = mktask(db, "ST-X1", "HT-S-X1")
    assert t.status == "excluded" and "尚未确认" in t.exclude_reason
    assert sla_adherence.eligible_tasks(db, "HT-S-X1", MONTH) == []

    c.confirmed = True
    db.commit()
    # 模拟合同确认端点的资格恢复逻辑
    t.status = "eligible"
    t.exclude_reason = ""
    db.commit()
    assert [x.id for x in sla_adherence.eligible_tasks(db, "HT-S-X1", MONTH)] == ["ST-X1"]


def test_scoring_requires_confirmed_contract(db):
    c = make_sla_contract(db, "HT-S-X2", confirmed=False)
    mktask(db, "ST-X2", "HT-S-X2")
    with pytest.raises(Exception) as exc:
        run(db, c)
    assert "尚未确认" in str(exc.value)


# ---------------------------------------------------------------- ② 三类违约判定


def test_missed_late_arrival_late_receipt_deductions(db):
    c = make_sla_contract(db)
    # 漏收：无到场
    t_missed = mktask(db, "ST-M1", when="2026-11-03 08:00", vehicle="沪A-M1", addr="漏收路1号")
    # 超时：迟到 25 分钟
    t_late = mktask(db, "ST-M2", when="2026-11-04 08:00", vehicle="沪A-M2", addr="超时路2号")
    mkarrival(db, "沪A-M2", "超时路2号", "2026-11-04 08:25", "ARR-M2")
    mksignoff(db, t_late, "2026-11-04 10:00", "SIG-M2", "沪A-M2")
    # 迟到签收：时限 24h，超 1.5 小时
    t_recv = mktask(db, "ST-M3", when="2026-11-05 07:30", vehicle="沪A-M3", addr="签收路3号")
    mkarrival(db, "沪A-M3", "签收路3号", "2026-11-05 07:35", "ARR-M3")
    mksignoff(db, t_recv, "2026-11-06 09:00", "SIG-M3", "沪A-M3")
    # 合规对照
    t_ok = mktask(db, "ST-M4", when="2026-11-06 09:00", vehicle="沪A-M4", addr="合规路4号")
    mkarrival(db, "沪A-M4", "合规路4号", "2026-11-06 09:05", "ARR-M4")
    mksignoff(db, t_ok, "2026-11-06 11:00", "SIG-M4", "沪A-M4")

    snap_missed = sla_adherence.evaluate_task(db, t_missed)
    assert {v["type"] for v in snap_missed["violations"]} == {"missed_collection"}
    assert snap_missed["deduction"] == 8.0

    snap_late = sla_adherence.evaluate_task(db, t_late)
    assert [v["type"] for v in snap_late["violations"]] == ["late_arrival"]
    assert snap_late["violations"][0]["evidence"]["delay_minutes"] == 25.0

    snap_recv = sla_adherence.evaluate_task(db, t_recv)
    assert [v["type"] for v in snap_recv["violations"]] == ["late_receipt"]
    assert snap_recv["violations"][0]["deduction"] == 2.0

    score = run(db, c)
    assert score.total_deduction == 13.0 and score.score == 87.0
    ok_snap = sla_adherence.evaluate_task(db, t_ok)
    assert ok_snap["violations"] == [] and ok_snap["evidence_complete"] is True


# ---------------------------------------------------------------- ③ 未核投诉只列风险


def test_unverified_complaint_is_risk_not_deduction(db):
    c = make_sla_contract(db)
    t = mktask(db, "ST-C1")
    mkarrival(db, "沪A-T1", "测试路1号", "2026-11-05 09:05", "ARR-C1")
    mksignoff(db, t, "2026-11-05 11:00", "SIG-C1")
    comp = mkcomplaint(db, "居民甲", "测试路1号", "2026-11-05 12:00")

    score = run(db, c)
    assert score.score == 100.0  # 未核不扣分
    assert any(r["type"] == "complaint_unverified" for r in score.risks)

    # 核实并关联任务后才计罚
    sla_complaints.verify_complaint(db, comp, task_id=t.id, note="核实", username="supervisor")
    score2 = run(db, c)
    assert score2.score == 95.0


def test_orphan_unverified_complaint_listed_as_risk(db):
    c = make_sla_contract(db)
    t = mktask(db, "ST-C2", when="2026-11-07 09:00")
    mkarrival(db, "沪A-T1", "测试路1号", "2026-11-07 09:05", "ARR-C2")
    mksignoff(db, t, "2026-11-07 11:00", "SIG-C2")
    mkcomplaint(db, "匿名", "无处可查街", "2026-11-07 08:00")
    score = run(db, c)
    assert score.score == 100.0
    assert any("无处可查街" in r["message"] for r in score.risks)


# ---------------------------------------------------------------- ④ 重复投诉合并去重


def test_duplicate_complaints_merge_into_single_penalty(db):
    c = make_sla_contract(db)
    t = mktask(db, "ST-G1")
    mkarrival(db, "沪A-T1", "测试路1号", "2026-11-05 09:05", "ARR-G1")
    mksignoff(db, t, "2026-11-05 11:00", "SIG-G1")
    c1 = mkcomplaint(db, "周阿姨", "测试路1号", "2026-11-05 10:30", "污水残留")
    c2 = mkcomplaint(db, "吴先生", "测试路1号", "2026-11-05 11:20", "同一摊污水")

    groups = sla_complaints.detect_merges(db)
    assert len(groups) == 1 and groups[0].status == "candidate"
    # 候选未确认前不计罚
    sla_complaints.verify_complaint(db, c1, task_id=t.id, note="核实", username="supervisor")
    sla_complaints.verify_complaint(db, c2, task_id=t.id, note="核实", username="supervisor")
    assert run(db, c).score == 100.0

    sla_complaints.decide_group(db, groups[0], confirm=True, reason="同一事件", username="supervisor")
    score = run(db, c)
    assert score.score == 95.0  # 整组只计一次 5 分
    snap = sla_adherence.evaluate_task(db, t)
    assert sum(1 for v in snap["violations"] if v["type"] == "valid_complaint") == 1


def test_dismissed_merge_group_counts_independently(db):
    c = make_sla_contract(db)
    t = mktask(db, "ST-G2")
    mkarrival(db, "沪A-T1", "测试路1号", "2026-11-05 09:05", "ARR-G2")
    mksignoff(db, t, "2026-11-05 11:00", "SIG-G2")
    c1 = mkcomplaint(db, "甲", "测试路1号", "2026-11-05 10:30")
    c2 = mkcomplaint(db, "乙", "测试路1号", "2026-11-05 11:20")
    groups = sla_complaints.detect_merges(db)
    sla_complaints.decide_group(db, groups[0], confirm=False, reason="并非同一事件", username="supervisor")
    db.refresh(c1)
    assert c1.merge_group_id is None  # 解除合并
    sla_complaints.verify_complaint(db, c1, task_id=t.id, note="独立成立", username="supervisor")
    sla_complaints.verify_complaint(db, c2, task_id=t.id, note="独立成立", username="supervisor")
    assert run(db, c).score == 90.0  # 两起独立投诉各计 5 分


def test_geo_radius_merge_with_different_address_text(db):
    c = make_sla_contract(db)
    mktask(db, "ST-G3")
    mkcomplaint(db, "甲", "小区东门", "2026-11-05 10:30", lat=31.2300, lon=121.4700)
    mkcomplaint(db, "乙", "垃圾收集点（东）", "2026-11-05 11:00", lat=31.2310, lon=121.4700)
    groups = sla_complaints.detect_merges(db)
    assert len(groups) == 1 and groups[0].relation == "geo_radius"


def test_three_reporters_same_event_single_penalty(db):
    # 三位居民在同一地址、时间窗内先后报告同一问题：并入同一组合，整组只计一次
    c = make_sla_contract(db)
    t = mktask(db, "ST-G4")
    mkarrival(db, "沪A-T1", "测试路1号", "2026-11-05 09:05", "ARR-G4")
    mksignoff(db, t, "2026-11-05 11:00", "SIG-G4")
    c1 = mkcomplaint(db, "甲", "测试路1号", "2026-11-05 10:00", "第一次")
    mkcomplaint(db, "乙", "测试路1号", "2026-11-05 10:40", "第二次")
    mkcomplaint(db, "丙", "测试路1号", "2026-11-05 11:30", "第三次")  # 距首报 90 分钟，仍在 120 分钟窗

    groups = sla_complaints.detect_merges(db)
    assert len(groups) == 1
    sla_complaints.decide_group(db, groups[0], confirm=True, reason="三人同报同一泄漏", username="supervisor")
    for comp in db.query(type(c1)).filter_by(merge_group_id=groups[0].id).all():
        sla_complaints.verify_complaint(db, comp, task_id=t.id, note="同组核实", username="supervisor")

    snap = sla_adherence.evaluate_task(db, t)
    assert sum(1 for v in snap["violations"] if v["type"] == "valid_complaint") == 1
    assert run(db, c).score == 95.0


# ---------------------------------------------------------------- ⑤ 申辩与主管裁定


def test_appeal_overturned_removes_upheld_keeps(db):
    c = make_sla_contract(db)
    t_up = mktask(db, "ST-A1", when="2026-11-08 08:00", vehicle="沪A-A1", addr="裁定路1号")
    mkarrival(db, "沪A-A1", "裁定路1号", "2026-11-08 08:20", "ARR-A1")  # 超时 20 分钟
    mksignoff(db, t_up, "2026-11-08 10:00", "SIG-A1", "沪A-A1")
    t_miss = mktask(db, "ST-A2", when="2026-11-09 08:00", vehicle="沪A-A2", addr="裁定路2号")

    # 采纳申辩 → 撤销超时
    ap1 = sla_appeals.create_appeal(db, t_up, violation_type="late_arrival", reason="交管备案")
    ruling = sla_appeals.rule_appeal(db, ap1, outcome="overturned", ruling_note="备案属实", username="supervisor")
    assert ruling.outcome == "overturned"
    snap = sla_adherence.evaluate_task(db, t_up)
    assert snap["violations"] == []
    assert snap["removed_violations"][0]["type"] == "late_arrival"

    # 驳回申辩 → 漏收维持
    ap2 = sla_appeals.create_appeal(db, t_miss, violation_type="missed_collection", reason="终端故障")
    sla_appeals.rule_appeal(db, ap2, outcome="upheld", ruling_note="无证据", username="supervisor")
    snap2 = sla_adherence.evaluate_task(db, t_miss)
    assert [v["type"] for v in snap2["violations"]] == ["missed_collection"]

    score = run(db, c)
    assert score.score == 92.0  # 只剩漏收 8 分
    # 申辩与裁定并列保存
    stored = db.query(CarrierAppeal).filter_by(task_id=t_up.id).first()
    assert stored.status == "overturned" and stored.decided_at is not None


def test_ruling_cannot_be_repeated(db):
    make_sla_contract(db)
    t = mktask(db, "ST-A3")
    ap = sla_appeals.create_appeal(db, t, violation_type="missed_collection", reason="x")
    sla_appeals.rule_appeal(db, ap, outcome="upheld", ruling_note="维持", username="supervisor")
    with pytest.raises(Exception):
        sla_appeals.rule_appeal(db, ap, outcome="overturned", ruling_note="重复", username="supervisor")


# ---------------------------------------------------------------- ⑥ 增量版本与受影响车次


def test_incremental_versions_track_affected_tasks(db):
    c = make_sla_contract(db)
    t1 = mktask(db, "ST-V1", when="2026-11-03 09:00", vehicle="沪A-V1", addr="版本路1号")
    mkarrival(db, "沪A-V1", "版本路1号", "2026-11-03 09:05", "ARR-V1")
    mksignoff(db, t1, "2026-11-03 11:00", "SIG-V1", "沪A-V1")

    v1 = run(db, c, note="首次")
    assert v1.version == 1 and v1.score == 100.0 and v1.affected_task_ids == ["ST-V1"]

    t2 = mktask(db, "ST-V2", when="2026-11-04 09:00", vehicle="沪A-V2", addr="版本路2号")
    v2 = run(db, c, note="新任务证据到达")
    assert v2.version == 2 and v2.score == 92.0 and v2.affected_task_ids == ["ST-V2"]

    # 无变化重算不制造空版本
    v3 = run(db, c)
    assert v3.version == 2
    assert db.query(ScoreVersion).filter_by(score_id=v1.id).count() == 2


# ---------------------------------------------------------------- ⑦ 月结锁定与迟到签收调整单


def test_month_end_lock_late_signoff_adjustment_keeps_voucher(db):
    c = make_sla_contract(db)
    t = mktask(db, "ST-L1", when="2026-11-05 09:00")
    mkarrival(db, "沪A-T1", "测试路1号", "2026-11-05 09:05", "ARR-L1")

    score = run(db, c, mode="month_end")
    assert score.score == 100.0
    # 月内进度暴露签收缺口
    progress = sla_scoring.month_progress(db, c, MONTH)
    assert progress["signoff_covered"] == 0 and progress["gaps"]

    sla_scoring.lock_score(db, score, "supervisor", "BATCH-2026-11-X")
    sla_scoring.mark_paid(db, score)
    # 锁定后不能重算
    with pytest.raises(Exception):
        run(db, c, mode="month_end")

    # 迟到签收在锁定后到达（时限 11-06 09:00，实际 11-07 09:00）
    sig = mksignoff(db, t, "2026-11-07 09:00", "SIG-LATE")
    adj = sla_scoring.register_locked_evidence(db, t, "late_signoff", signoff=sig, username="analyst")
    assert adj is not None and adj.status == "open" and adj.delta_deduction == 2.0
    db.refresh(sig)
    assert sig.adjusted is True

    db.refresh(score)
    assert score.score == 100.0 and score.payment_status == "paid"  # 原凭证不改写
    sla_scoring.apply_adjustment(db, adj, "supervisor")
    view = sla_scoring.score_to_dict(db, score)
    assert view["adjusted_score"] == 98.0 and view["open_adjustment_count"] == 0


# ---------------------------------------------------------------- ⑧ 已支付追溯变更走负差额调整


def test_ruling_after_paid_creates_negative_adjustment(db):
    c = make_sla_contract(db)
    t = mktask(db, "ST-P1", when="2026-11-10 09:00")  # 漏收
    score = run(db, c, mode="month_end")
    assert score.score == 92.0
    sla_scoring.lock_score(db, score, "supervisor", "BATCH-P1")
    sla_scoring.mark_paid(db, score)

    ap = sla_appeals.create_appeal(db, t, violation_type="missed_collection", reason="补放到场记录")
    ruling = sla_appeals.rule_appeal(db, ap, outcome="overturned", ruling_note="证据补足，撤销", username="supervisor")

    adj = db.query(LockAdjustment).filter_by(evidence_ref=f"ruling-{ruling.id}").first()
    assert adj is not None and adj.delta_deduction == -8.0  # 返还
    db.refresh(score)
    assert score.score == 92.0  # 原分数/付款不动
    sla_scoring.apply_adjustment(db, adj, "supervisor")
    assert sla_scoring.score_to_dict(db, score)["adjusted_score"] == 100.0


# ---------------------------------------------------------------- ⑨ 并发：申辩裁定与评分发布


def test_concurrent_ruling_requires_review_then_rerun_clears(db):
    c = make_sla_contract(db)
    t = mktask(db, "ST-R1", when="2026-11-11 08:00", vehicle="沪A-R1", addr="并发路1号")
    mkarrival(db, "沪A-R1", "并发路1号", "2026-11-11 08:20", "ARR-R1")  # 超时
    mksignoff(db, t, "2026-11-11 10:00", "SIG-R1", "沪A-R1")
    score = run(db, c)
    assert score.score == 97.0
    sla_scoring.publish_score(db, score, "analyst")

    ap = sla_appeals.create_appeal(db, t, violation_type="late_arrival", reason="门禁延迟")
    sla_appeals.rule_appeal(db, ap, outcome="upheld", ruling_note="维持", username="supervisor")
    db.refresh(score)
    assert score.review_required is True and "ST-R1" in score.review_reason
    # 待复查期间不能发布
    with pytest.raises(Exception):
        sla_scoring.publish_score(db, score, "analyst")

    # 主管重新查看影响车次后重算 → 标记清除
    rerun = run(db, c, note="主管重新查看影响车次")
    assert rerun.review_required is False and rerun.score == 97.0
    sla_scoring.publish_score(db, rerun, "analyst")  # 不再阻塞


def test_lock_blocked_when_appeal_pending(db):
    c = make_sla_contract(db)
    t = mktask(db, "ST-R2")
    sla_appeals.create_appeal(db, t, violation_type="missed_collection", reason="待裁")
    score = run(db, c, mode="month_end")
    with pytest.raises(Exception) as exc:
        sla_scoring.lock_score(db, score, "supervisor", "BATCH-R2")
    assert "待主管裁定" in str(exc.value)


# ---------------------------------------------------------------- ⑩ 下钻载荷、哈希与权限


def test_drilldown_payload_and_content_hash(db):
    make_sla_contract(db)
    t = mktask(db, "ST-D1")
    arr = mkarrival(db, "沪A-T1", "测试路1号", "2026-11-05 09:05", "ARR-D1")
    sig = mksignoff(db, t, "2026-11-05 11:00", "SIG-D1")
    # 哈希可重算
    assert arr.content_hash == sla_evidence.content_hash(
        {"kind": "vehicle_arrival", "vehicle_id": "沪A-T1", "address": "测试路1号",
         "arrival_time": arr.arrival_time.isoformat(), "source_ref": "ARR-D1",
         "raw_text": "到场流水 ARR-D1"}
    )
    assert len(sig.content_hash) == 64

    snap = sla_adherence.evaluate_task(db, t)
    assert snap["contract_id"] == "HT-S-001"
    assert snap["arrival"]["event_id"] == arr.id and snap["signoff"]["event_id"] == sig.id


def test_api_roles_and_drilldown(db):
    make_users(db)
    db.add(User(username="supervisor", password_hash=make_password_hash("pw123456"),
                role="supervisor", display_name="主管"))
    db.commit()
    c = make_sla_contract(db)
    t = mktask(db, "ST-API-1")
    mkarrival(db, "沪A-T1", "测试路1号", "2026-11-05 09:05", "ARR-API")
    mksignoff(db, t, "2026-11-05 11:00", "SIG-API")

    client = TestClient(app)
    assert client.get("/api/sla/tasks").status_code == 401
    analyst = client.post("/api/auth/login", json={"username": "analyst", "password": "pw123456"}).json()
    auditor = client.post("/api/auth/login", json={"username": "auditor", "password": "pw123456"}).json()
    sup = client.post("/api/auth/login", json={"username": "supervisor", "password": "pw123456"}).json()
    ah, uh, sh = ({"Authorization": f"Bearer {x['token']}"} for x in (analyst, auditor, sup))

    # 只读角色不能运行评分
    r = client.post("/api/sla/scoring/run", headers=uh,
                    json={"contract_id": c.id, "month": MONTH, "mode": "incremental"})
    assert r.status_code == 403
    # 分析员不能月结锁定（主管权限）
    client.post("/api/sla/scoring/run", headers=ah,
                json={"contract_id": c.id, "month": MONTH, "mode": "month_end"})
    r = client.post("/api/sla/scoring/lock", headers=ah,
                    json={"contract_id": c.id, "month": MONTH, "mode": "month_end"})
    assert r.status_code == 403
    # 主管锁定成功
    r = client.post("/api/sla/scoring/lock", headers=sh,
                    json={"contract_id": c.id, "month": MONTH, "mode": "month_end",
                          "evidence_batch_ref": "BATCH-API"})
    assert r.status_code == 200 and r.json()["status"] == "locked"

    # 下钻：合同条款 + 任务证据 + 合规快照
    detail = client.get(f"/api/sla/tasks/{t.id}", headers=uh).json()
    assert detail["contract_clause"]["sla_rules"]["receipt_due_hours"] == 24
    assert detail["arrivals"][0]["source_ref"] == "ARR-API"
    assert detail["signoffs"][0]["source_ref"] == "SIG-API"
    assert detail["compliance_snapshot"]["evidence_complete"] is True

    # 评分视图含版本流水与调整单数组
    view = client.get(f"/api/sla/scoring?contract_id={c.id}&month={MONTH}&mode=month_end", headers=uh).json()
    assert view["versions"] and "adjustments" in view and view["evidence_batch_ref"] == "BATCH-API"
