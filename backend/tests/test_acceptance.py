"""验收测试：六个合同场景 + 费率修订版本化 + 证据哈希 + 权限与 API 冒烟。"""

from fastapi.testclient import TestClient

from app.main import app
from app.models import AmountFact, BatchLink, MeasurementFact, Publication
from app.services import adjudicator, compiler, departure, evidence, publisher, registry
from app.services import calibration as calibration_svc
from tests.conftest import (
    add_event,
    add_position,
    link_confirmed,
    make_batch,
    make_calibration,
    make_catalog,
    make_contract,
    make_users,
)

# ---------------------------------------------------------------- 场景① 主计量点合同


def test_priority_primary_metering(db):
    make_contract(db, priority=["facility_receipt", "weighbridge_reading", "gate_record"])
    make_catalog(db)
    make_calibration(db, "WB-01", 2)
    b = make_batch(db)

    link_confirmed(db, b, add_event(db, "collection_task", when="2026-10-12 08:55"), "task")
    link_confirmed(db, b, add_event(db, "gate_record", 12.50, "2026-10-12 09:05", "GATE-EAST"), "gate")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 12.40, "2026-10-12 09:12", "WB-01", 2), "weighing")
    receipt = add_event(db, "facility_receipt", 12.35, "2026-10-12 09:40", "FAC-01-SCALE", site="FAC-01")
    link_confirmed(db, b, receipt, "receipt")

    fact = adjudicator.adjudicate(db, b)
    assert fact.status == "issued"
    assert fact.billing_weight == 12.35          # 签收为主计量点
    assert fact.selected_event_id == receipt.id
    # 其余合格来源作为差异佐证
    assert len(fact.discrepancy) == 2
    by_source = {d["source_type"]: d for d in fact.discrepancy}
    assert by_source["weighbridge_reading"]["diff_kg"] == 50.0
    assert by_source["gate_record"]["diff_kg"] == 150.0

    amount = compiler.compile_amounts(db, b)
    assert amount.catalog_version == 1
    assert amount.amount == 1023.25              # 10×80 + 2.35×95
    assert amount.amount_low is None

    pub = publisher.publish(db, b)
    assert pub.state == "published"
    assert pub.billing_basis["amount"] == 1023.25
    assert pub.billing_basis["catalog_version"] == 1
    assert pub.billing_basis["selected_event_id"] == receipt.id
    assert pub.downstream_ref.endswith("-v1")


# ---------------------------------------------------------------- 场景② 跨场称重差异（区间重叠）


def test_interval_cross_site_overlap(db):
    make_contract(db, "HT-T-002", strategy="interval",
                  priority=["weighbridge_reading"], tol_min=60, qtol=0.005)
    make_catalog(db)
    make_calibration(db, "WB-01", 2)
    make_calibration(db, "WB-03", 1)
    b = make_batch(db, "PC-T-002", "HT-T-002", "沪B-T002", "2026-10-15 14:00")

    link_confirmed(db, b, add_event(db, "collection_task", when="2026-10-15 14:00"), "task")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 18.20, "2026-10-15 14:10", "WB-01", 2), "weighing")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 18.05, "2026-10-15 14:35", "WB-03", 1, "TRANSFER"), "weighing")

    fact = adjudicator.adjudicate(db, b)
    assert fact.status == "issued"
    assert fact.billing_weight is None           # 区间裁定不签发单点重量
    assert fact.acceptable_low == 18.109
    assert fact.acceptable_high == 18.14025

    amount = compiler.compile_amounts(db, b)
    assert amount.amount is None                 # 未决范围，不伪造已确认金额
    assert amount.amount_low == compiler.tiered_amount(18.109, amount_tiers(db))
    assert amount.amount_high == compiler.tiered_amount(18.14025, amount_tiers(db))
    assert amount.cross_tier is False            # 上下界同处第二阶梯

    pub = publisher.publish(db, b)
    assert pub.state == "published"
    assert pub.pending_ranges["amount_low"] == amount.amount_low
    assert pub.pending_ranges["amount_high"] == amount.amount_high


def amount_tiers(db):
    from app.models import RateCatalog

    return db.query(RateCatalog).filter_by(version=1).first().tiers


# ---------------------------------------------------------------- 场景③ 签收后离场


def _build_batch_with_receipt(db):
    make_contract(db)
    make_catalog(db)
    make_calibration(db, "WB-01", 2)
    b = make_batch(db, "PC-T-003", "HT-T-001", "沪A-T003", "2026-10-18 08:50")
    link_confirmed(db, b, add_event(db, "collection_task", when="2026-10-18 08:50"), "task")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 9.80, "2026-10-18 09:08", "WB-01", 2), "weighing")
    link_confirmed(db, b, add_event(db, "facility_receipt", 9.75, "2026-10-18 09:35", "FAC-01-SCALE", site="FAC-01"), "receipt")
    return b


def test_departure_clock_correction_not_contradiction(db):
    b = _build_batch_with_receipt(db)
    # 车载终端时钟快 30 分钟：原始最后定位 10:07 晚于签收 09:35
    add_position(db, "沪A-T003", "FAC-01", "2026-10-18 09:00", "2026-10-18 09:40", offset=1800)
    add_position(db, "沪A-T003", "ROAD-S7", "2026-10-18 10:05", "2026-10-18 10:07", offset=1800)

    check = departure.check_departure(db, b, tolerance_minutes=10)
    assert check.result == "consistent"          # 不能仅以最后定位晚于签收认定矛盾
    assert check.detail["supporting_segment_ids"]
    assert all(p["explained_by_clock"] for p in check.detail["late_positions"])


def test_departure_true_contradiction_and_insufficient(db):
    b = _build_batch_with_receipt(db)
    add_position(db, "沪A-T003", "FAC-01", "2026-10-18 09:00", "2026-10-18 09:40", offset=1800)
    # 时钟无偏移且校正后仍远超容差 → 矛盾
    add_position(db, "沪A-T003", "ROAD-S7", "2026-10-18 11:20", "2026-10-18 11:25", offset=0)
    check = departure.check_departure(db, b, tolerance_minutes=10)
    assert check.result == "contradiction"

    b2 = make_batch(db, "PC-T-003B", "HT-T-001", "沪A-T003B", "2026-10-18 08:50")
    link_confirmed(db, b2, add_event(db, "collection_task", when="2026-10-18 08:50"), "task")
    link_confirmed(db, b2, add_event(db, "facility_receipt", 9.75, "2026-10-18 09:35", "FAC-01-SCALE", site="FAC-01"), "receipt")
    add_position(db, "沪A-T003B", "ROAD-S7", "2026-10-18 10:05", "2026-10-18 10:07", offset=1800)
    check2 = departure.check_departure(db, b2, tolerance_minutes=10)
    assert check2.result == "insufficient"       # 缺少支持到场的定位片段


# ---------------------------------------------------------------- 场景④ 校准更正


def test_calibration_correction_triggers_review(db):
    make_contract(db, "HT-T-004", priority=["weighbridge_reading", "facility_receipt"])
    make_catalog(db)
    cal = make_calibration(db, "WB-02", 3)
    b = make_batch(db, "PC-T-004", "HT-T-004", "沪C-T004", "2026-10-20 09:55")

    link_confirmed(db, b, add_event(db, "collection_task", when="2026-10-20 09:55"), "task")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 22.00, "2026-10-20 10:05", "WB-02", 3, "WEST"), "weighing")
    link_confirmed(db, b, add_event(db, "facility_receipt", 21.90, "2026-10-20 10:30", "FAC-01-SCALE", site="FAC-01"), "receipt")

    fact = adjudicator.adjudicate(db, b)
    assert fact.billing_weight == 22.00
    amount = compiler.compile_amounts(db, b)
    assert amount.amount == 1970.00
    pub1 = publisher.publish(db, b)
    assert pub1.state == "published"

    # 检定机构更正：v3 期间示值偏低 2%
    successor, affected = calibration_svc.correct_calibration(db, cal, factor=1.02)
    assert successor.version == 4
    assert affected == [b.id]
    db.refresh(fact)
    assert fact.status == "under_review"         # 受影响计量事实转为复审中
    db.refresh(b)
    assert b.status == "under_review"

    pub2 = publisher.publish(db, b)              # 新计费视图暂停引用
    assert pub2.state == "paused"
    assert pub2.billing_basis is None
    db.refresh(pub1)
    assert pub1.billing_basis["amount"] == 1970.00   # 旧账务视图保留原依据

    # 复审：重裁定适用更正系数 → 新事实版本
    fact2 = adjudicator.adjudicate(db, b)
    assert fact2.version == 2 and fact2.status == "issued"
    assert fact2.billing_weight == 22.44         # 22.00 × 1.02
    assert any(r.get("corrected_to") for r in fact2.calibration_refs)

    amount2 = compiler.compile_amounts(db, b)
    assert amount2.interpretation_version == 2
    assert amount2.amount == compiler.tiered_amount(22.44, amount_tiers(db))
    db.refresh(amount)
    assert amount.status == "superseded" and amount.amount == 1970.00  # 历史不回写

    pub3 = publisher.publish(db, b)
    assert pub3.state == "published" and pub3.version == 3


# ---------------------------------------------------------------- 场景⑤ 双批次争用


def test_dual_batch_contention_freezes_allocation(db):
    make_contract(db)
    make_catalog(db)
    make_calibration(db, "WB-03", 1)
    ba = make_batch(db, "PC-T-005A", "HT-T-001", "沪D-T005A", "2026-10-22 10:00")
    bb = make_batch(db, "PC-T-005B", "HT-T-001", "沪E-T005B", "2026-10-22 10:05")

    link_confirmed(db, ba, add_event(db, "collection_task", when="2026-10-22 10:00"), "task")
    link_confirmed(db, bb, add_event(db, "collection_task", when="2026-10-22 10:05"), "task")
    shared = add_event(db, "weighbridge_reading", 23.10, "2026-10-22 10:12", "WB-03", 1, "TRANSFER", plate="待核")

    registry.add_link(db, ba, shared.id, "weighing")
    registry.add_link(db, bb, shared.id, "weighing")
    frozen = db.query(BatchLink).filter_by(event_id=shared.id, status="frozen").all()
    assert len(frozen) == 2                      # 关系册冻结分配

    link_a = db.query(BatchLink).filter_by(batch_id=ba.id, event_id=shared.id).first()
    link_b = db.query(BatchLink).filter_by(batch_id=bb.id, event_id=shared.id).first()
    registry.confirm_link(db, link_a, "车牌识别核实归属 005A")
    db.refresh(link_b)
    assert link_b.status == "frozen"             # 另一批次仍冻结，直到证据确定
    registry.reject_link(db, link_b, "归属 005A 已确定，排除本批次候选")

    link_confirmed(db, ba, add_event(db, "facility_receipt", 23.02, "2026-10-22 10:40", "FAC-01-SCALE", site="FAC-01"), "receipt")
    fact_a = adjudicator.adjudicate(db, ba)
    assert fact_a.status == "issued" and fact_a.billing_weight == 23.02

    fact_b = adjudicator.adjudicate(db, bb)
    assert fact_b.status == "pending_conflict"   # 争用败方无有效计量来源
    assert fact_b.conflicts[0]["type"] == "no_qualified_source"


# ---------------------------------------------------------------- 场景⑥ 跨阶梯区间


def test_cross_tier_interval_boundary_amounts(db):
    make_contract(db, "HT-T-006", strategy="interval",
                  priority=["weighbridge_reading"], tol_min=60, qtol=0.005)
    make_catalog(db)
    make_calibration(db, "WB-01", 2)
    make_calibration(db, "WB-03", 1)
    b = make_batch(db, "PC-T-006", "HT-T-006", "沪F-T006", "2026-10-25 11:00")

    link_confirmed(db, b, add_event(db, "collection_task", when="2026-10-25 11:00"), "task")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 9.98, "2026-10-25 11:10", "WB-01", 2), "weighing")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 10.05, "2026-10-25 11:35", "WB-03", 1, "TRANSFER"), "weighing")

    fact = adjudicator.adjudicate(db, b)
    assert fact.status == "issued"
    assert fact.acceptable_low == 9.99975
    assert fact.acceptable_high == 10.0299       # 跨越 10t 阶梯边界

    amount = compiler.compile_amounts(db, b)
    assert amount.cross_tier is True
    assert amount.amount is None                 # 不取中点伪造已确认金额
    assert amount.amount_low == 799.98           # 9.99975 × 80
    assert amount.amount_high == 802.84          # 800 + 0.0299 × 95
    midpoint = round((amount.amount_low + amount.amount_high) / 2, 2)
    assert amount.amount != midpoint

    pub = publisher.publish(db, b)
    assert pub.state == "published"
    assert pub.pending_ranges["cross_tier"] is True
    assert pub.billing_basis["amount"] is None


# ---------------------------------------------------------------- 费率目录修订：新解释版本，不回写历史


def test_catalog_revision_generates_new_interpretation(db):
    make_contract(db)
    make_catalog(db, version=1, effective="2026-01-01 00:00", effective_to="2026-11-01 00:00")
    make_calibration(db, "WB-01", 2)
    b = make_batch(db, "PC-T-007", "HT-T-001", "沪G-T007", "2026-10-20 09:00")

    link_confirmed(db, b, add_event(db, "collection_task", when="2026-10-20 09:00"), "task")
    link_confirmed(db, b, add_event(db, "facility_receipt", 12.00, "2026-10-20 09:30", "FAC-01-SCALE", site="FAC-01"), "receipt")
    adjudicator.adjudicate(db, b)
    a1 = compiler.compile_amounts(db, b)
    assert a1.catalog_version == 1 and a1.amount == 990.00   # 800 + 2×95

    # 服务发生后目录修订（11 月新价）
    make_catalog(db, version=2, effective="2026-11-01 00:00",
                 tiers=[{"up_to": 10, "rate": 85.0}, {"up_to": 20, "rate": 100.0}, {"up_to": None, "rate": 118.0}])
    a2 = compiler.compile_amounts(db, b)
    assert a2.interpretation_version == 2
    assert a2.catalog_version == 1               # 仍以服务当时有效目录为准
    assert a2.amount == 990.00
    db.refresh(a1)
    assert a1.status == "superseded" and a1.amount == 990.00  # 历史解释版本不回写


# ---------------------------------------------------------------- 区间不重叠 → 已批准换算依据


def test_interval_approved_conversion_basis(db):
    make_contract(db, "HT-T-008", strategy="interval", priority=["weighbridge_reading"],
                  tol_min=60, qtol=0.005, conv={"factor": 0.98, "note": "场地皮重差换算"})
    make_catalog(db)
    make_calibration(db, "WB-01", 2)
    make_calibration(db, "WB-03", 1)
    b = make_batch(db, "PC-T-008", "HT-T-008", "沪H-T008", "2026-10-26 11:00")

    link_confirmed(db, b, add_event(db, "collection_task", when="2026-10-26 11:00"), "task")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 10.00, "2026-10-26 11:10", "WB-01", 2), "weighing")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 10.30, "2026-10-26 11:35", "WB-03", 1, "TRANSFER"), "weighing")

    fact = adjudicator.adjudicate(db, b)
    assert fact.status == "issued"               # 换算后重叠 → 形成计费事实
    assert "换算" in fact.note
    assert fact.acceptable_low is not None and fact.acceptable_low <= fact.acceptable_high


def test_interval_disjoint_without_conversion_pending(db):
    make_contract(db, "HT-T-009", strategy="interval", priority=["weighbridge_reading"], tol_min=60)
    make_catalog(db)
    make_calibration(db, "WB-01", 2)
    make_calibration(db, "WB-03", 1)
    b = make_batch(db, "PC-T-009", "HT-T-009", "沪I-T009", "2026-10-26 11:00")

    link_confirmed(db, b, add_event(db, "collection_task", when="2026-10-26 11:00"), "task")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 10.00, "2026-10-26 11:10", "WB-01", 2), "weighing")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 11.00, "2026-10-26 11:35", "WB-03", 1, "TRANSFER"), "weighing")

    fact = adjudicator.adjudicate(db, b)
    assert fact.status == "pending_conflict"
    assert fact.conflicts[0]["type"] == "interval_disjoint"


# ---------------------------------------------------------------- 证据哈希与权限


def test_evidence_hash_stable_and_content_addressed(db):
    e = add_event(db, "weighbridge_reading", 12.40, "2026-10-12 09:12", "WB-01", 2)
    again = evidence.compute_hash(
        "weighbridge_reading", e.raw_text, e.event_time, "WB-01", "EAST", 2, e.payload
    )
    assert e.content_hash == again and len(e.content_hash) == 64


def test_api_auth_roles_and_end_to_end(db):
    make_users(db)
    make_contract(db)
    make_catalog(db)
    make_calibration(db, "WB-01", 2)
    client = TestClient(app)

    assert client.get("/api/batches").status_code == 401

    auditor = client.post("/api/auth/login", json={"username": "auditor", "password": "pw123456"}).json()
    analyst = client.post("/api/auth/login", json={"username": "analyst", "password": "pw123456"}).json()
    ah = {"Authorization": f"Bearer {analyst['token']}"}
    uh = {"Authorization": f"Bearer {auditor['token']}"}

    # 只读角色不能写
    r = client.post("/api/batches", headers=uh, json={
        "id": "PC-API-001", "contract_id": "HT-T-001",
        "vehicle_id": "沪A-API1", "service_time": "2026-10-12T08:55:00+08:00"})
    assert r.status_code == 403

    # 分析员端到端：建批次 → 录证据 → 关联 → 裁定 → 编译 → 发布 → 月报
    assert client.post("/api/batches", headers=ah, json={
        "id": "PC-API-001", "contract_id": "HT-T-001",
        "vehicle_id": "沪A-API1", "service_time": "2026-10-12T08:55:00+08:00"}).status_code == 201

    def post_ev(st, weight, when, device=None, cal=None, site="EAST"):
        payload = {"plate": "沪A-API1"}
        if weight is not None:
            payload["weight_tons"] = weight
        r = client.post("/api/evidence", headers=ah, json={
            "source_type": st, "raw_text": f"{st} 原文", "event_time": f"2026-10-12T{when}:00+08:00",
            "device_id": device, "site_id": site, "calibration_version": cal, "payload": payload})
        assert r.status_code == 201
        return r.json()["id"]

    t = post_ev("collection_task", None, "08:55")
    w = post_ev("weighbridge_reading", 12.40, "09:12", "WB-01", 2)
    rcp = post_ev("facility_receipt", 12.35, "09:40", "FAC-01-SCALE", None, "FAC-01")

    for eid, lt in [(t, "task"), (w, "weighing"), (rcp, "receipt")]:
        r = client.post("/api/batches/PC-API-001/links", headers=ah, json={"event_id": eid, "link_type": lt})
        lid = r.json()["id"]
        assert client.post(f"/api/links/{lid}/confirm", headers=ah, json={"reason": "核"}).status_code == 200

    fact = client.post("/api/batches/PC-API-001/adjudicate", headers=ah).json()
    assert fact["status"] == "issued" and fact["billing_weight"] == 12.35
    amount = client.post("/api/batches/PC-API-001/compile", headers=ah).json()
    assert amount["amount"] == 1023.25
    pub = client.post("/api/batches/PC-API-001/publish", headers=ah).json()
    assert pub["state"] == "published" and pub["downstream_ref"] == "PUB-PC-API-001-v1"

    report = client.get("/api/reports/monthly?month=2026-10", headers=uh).json()
    row = next(r for r in report["batches"] if r["batch_id"] == "PC-API-001")
    assert row["confirmed_amount"] == 1023.25
    assert row["strategy_label"] == "优先级裁定"

    detail = client.get("/api/batches/PC-API-001", headers=uh).json()
    # 每项费用结论可回查：重量、关联、合同策略、费率、校准与版本
    basis = detail["publications"][0]["billing_basis"]
    assert basis["billing_weight"] == 12.35
    assert basis["catalog_version"] == 1
    assert basis["strategy"] == "priority"
    assert detail["links"] and detail["measurement_facts"][0]["calibration_refs"]
