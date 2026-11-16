"""演示种子数据：覆盖六个验收场景。幂等——已存在合同 HT-2026-001 时跳过。

场景：① 主计量点合同 ② 跨场称重差异（区间重叠） ③ 签收后离场（时钟校正）
      ④ 校准更正（复审中） ⑤ 双批次争用（冻结分配） ⑥ 跨阶梯区间（边界金额）
"""

from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import (
    Batch,
    CalibrationRecord,
    Contract,
    PositionSegment,
    RateCatalog,
    User,
)
from app.security import make_password_hash
from app.services import adjudicator, compiler, departure, evidence, publisher, registry
from app.services import calibration as calibration_svc

CST = timezone(timedelta(hours=8))


def dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=CST)


def seed():
    db = SessionLocal()
    try:
        if db.get(Contract, "HT-2026-001"):
            return  # 已初始化

        # ---------------- 用户（测试账号） ----------------
        db.add_all(
            [
                User(
                    username="analyst",
                    password_hash=make_password_hash("Analyst@2026"),
                    role="analyst",
                    display_name="数据团队·分析员",
                ),
                User(
                    username="auditor",
                    password_hash=make_password_hash("Auditor@2026"),
                    role="auditor",
                    display_name="财务·复核员",
                ),
            ]
        )

        # ---------------- 合同 ----------------
        db.add_all(
            [
                Contract(
                    id="HT-2026-001",
                    name="城东生活垃圾收运合同（主计量点）",
                    contract_type="主计量点合同",
                    strategy="priority",
                    source_priority=["facility_receipt", "weighbridge_reading", "gate_record"],
                    time_tolerance_minutes=45,
                    quality_tolerance_pct=0.005,
                ),
                Contract(
                    id="HT-2026-002",
                    name="跨区转运计量合同（区间裁定）",
                    contract_type="区间裁定合同",
                    strategy="interval",
                    source_priority=["weighbridge_reading", "facility_receipt"],
                    time_tolerance_minutes=60,
                    quality_tolerance_pct=0.005,
                ),
                Contract(
                    id="HT-2026-004",
                    name="西场固废收运合同（主计量点·地磅）",
                    contract_type="主计量点合同",
                    strategy="priority",
                    source_priority=["weighbridge_reading", "facility_receipt", "gate_record"],
                    time_tolerance_minutes=45,
                    quality_tolerance_pct=0.005,
                ),
            ]
        )

        # ---------------- 费率目录（冻结；v2 为服务后修订版） ----------------
        db.add_all(
            [
                RateCatalog(
                    version=1,
                    effective_from=dt("2026-01-01 00:00"),
                    effective_to=dt("2026-11-01 00:00"),
                    frozen=True,
                    tiers=[
                        {"up_to": 10, "rate": 80.0},
                        {"up_to": 20, "rate": 95.0},
                        {"up_to": None, "rate": 110.0},
                    ],
                    note="2026 年度处理费目录（累进阶梯，元/吨）",
                ),
                RateCatalog(
                    version=2,
                    effective_from=dt("2026-11-01 00:00"),
                    effective_to=None,
                    frozen=True,
                    tiers=[
                        {"up_to": 10, "rate": 85.0},
                        {"up_to": 20, "rate": 100.0},
                        {"up_to": None, "rate": 118.0},
                    ],
                    note="2026-11 修订：上调各阶梯单价（不回写 10 月服务）",
                ),
            ]
        )

        # ---------------- 校准记录 ----------------
        cal_wb01 = CalibrationRecord(
            device_id="WB-01", version=2, effective_from=dt("2026-01-01 00:00"),
            factor=1.0, status="active", note="东场地磅周期检定",
        )
        cal_wb02 = CalibrationRecord(
            device_id="WB-02", version=3, effective_from=dt("2026-01-01 00:00"),
            factor=1.0, status="active", note="西场地磅周期检定",
        )
        cal_wb03 = CalibrationRecord(
            device_id="WB-03", version=1, effective_from=dt("2026-01-01 00:00"),
            factor=1.0, status="active", note="转运站地磅周期检定",
        )
        db.add_all([cal_wb01, cal_wb02, cal_wb03])
        db.commit()

        def ev(**kw):
            return evidence.ingest(db, **kw)

        def mk_batch(bid, cid, vehicle, when):
            b = Batch(id=bid, contract_id=cid, vehicle_id=vehicle, service_time=dt(when))
            db.add(b)
            db.commit()
            return b

        def link(batch, event, ltype, confirm=True):
            l = registry.add_link(db, batch, event.id, ltype)
            if confirm and l.status == "candidate":
                l = registry.confirm_link(db, l, "种子数据：证据链完整")
            return l

        # ================ 场景① 主计量点合同（签收为主计量点） ================
        b1 = mk_batch("PC-20261012-001", "HT-2026-001", "沪A-D2101", "2026-10-12 08:55")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1012-01：城东 3 号线，车 沪A-D2101",
               event_time=dt("2026-10-12 08:55"), site_id="EAST-LINE-3",
               payload={"task_no": "RW-1012-01", "plate": "沪A-D2101"})
        link(b1, e, "task")
        e = ev(source_type="gate_record", raw_text="东门岗记录：沪A-D2101 进场，估重 12.50t",
               event_time=dt("2026-10-12 09:05"), device_id="GATE-EAST", site_id="EAST",
               payload={"weight_tons": 12.50, "plate": "沪A-D2101"})
        link(b1, e, "gate")
        e = ev(source_type="weighbridge_reading", raw_text="东场地磅 WB-01：毛重 21.40t 皮重 9.00t 净重 12.40t",
               event_time=dt("2026-10-12 09:12"), device_id="WB-01", site_id="EAST",
               calibration_version=2, payload={"weight_tons": 12.40, "plate": "沪A-D2101"})
        link(b1, e, "weighing")
        e = ev(source_type="facility_receipt", raw_text="处理场 FAC-01 签收单：净重 12.35t，批次合格",
               event_time=dt("2026-10-12 09:40"), device_id="FAC-01-SCALE", site_id="FAC-01",
               payload={"weight_tons": 12.35, "plate": "沪A-D2101"})
        link(b1, e, "receipt")
        adjudicator.adjudicate(db, b1)          # 签收 12.35 选定，地磅/门岗为差异佐证
        compiler.compile_amounts(db, b1)        # 目录 v1：10×80 + 2.35×95 = 1023.25
        publisher.publish(db, b1)               # PUB-PC-20261012-001-v1
        ev(source_type="accounting_receipt", raw_text="账务回执 FV-20261031-001：确认金额 1023.25 元",
           event_time=dt("2026-10-31 18:00"), site_id="FINANCE",
           payload={"voucher": "FV-20261031-001", "amount": 1023.25, "ref": "PUB-PC-20261012-001-v1"})

        # ================ 场景② 跨场称重差异（区间重叠） ================
        b2 = mk_batch("PC-20261015-002", "HT-2026-002", "沪B-D3302", "2026-10-15 14:00")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1015-02：跨区转运，车 沪B-D3302",
               event_time=dt("2026-10-15 14:00"), site_id="CROSS-1",
               payload={"task_no": "RW-1015-02", "plate": "沪B-D3302"})
        link(b2, e, "task")
        e = ev(source_type="weighbridge_reading", raw_text="东场地磅 WB-01：净重 18.20t",
               event_time=dt("2026-10-15 14:10"), device_id="WB-01", site_id="EAST",
               calibration_version=2, payload={"weight_tons": 18.20, "plate": "沪B-D3302"})
        link(b2, e, "weighing")
        e = ev(source_type="weighbridge_reading", raw_text="转运站地磅 WB-03：净重 18.05t",
               event_time=dt("2026-10-15 14:35"), device_id="WB-03", site_id="TRANSFER",
               calibration_version=1, payload={"weight_tons": 18.05, "plate": "沪B-D3302"})
        link(b2, e, "weighing")
        adjudicator.adjudicate(db, b2)          # 区间 [18.109, 18.14025] 重叠 → 签发区间
        compiler.compile_amounts(db, b2)        # 边界金额 1570.35 / 1573.32，不跨阶梯
        publisher.publish(db, b2)

        # ================ 场景③ 签收后离场（时钟校正后不认定矛盾） ================
        b3 = mk_batch("PC-20261018-003", "HT-2026-001", "沪A-D2101", "2026-10-18 08:50")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1018-03：城东 3 号线",
               event_time=dt("2026-10-18 08:50"), site_id="EAST-LINE-3",
               payload={"task_no": "RW-1018-03", "plate": "沪A-D2101"})
        link(b3, e, "task")
        e = ev(source_type="gate_record", raw_text="东门岗记录：沪A-D2101 进场，估重 9.90t",
               event_time=dt("2026-10-18 09:00"), device_id="GATE-EAST", site_id="EAST",
               payload={"weight_tons": 9.90, "plate": "沪A-D2101"})
        link(b3, e, "gate")
        e = ev(source_type="weighbridge_reading", raw_text="东场地磅 WB-01：净重 9.80t",
               event_time=dt("2026-10-18 09:08"), device_id="WB-01", site_id="EAST",
               calibration_version=2, payload={"weight_tons": 9.80, "plate": "沪A-D2101"})
        link(b3, e, "weighing")
        e = ev(source_type="facility_receipt", raw_text="处理场 FAC-01 签收单：净重 9.75t",
               event_time=dt("2026-10-18 09:35"), device_id="FAC-01-SCALE", site_id="FAC-01",
               payload={"weight_tons": 9.75, "plate": "沪A-D2101"})
        link(b3, e, "receipt")
        # 车载终端时钟快 30 分钟：原始最后定位 10:07 晚于签收 09:35，校正后 09:37 在容差内
        db.add_all(
            [
                PositionSegment(vehicle_id="沪A-D2101", site_id="FAC-01",
                                start_time=dt("2026-10-18 09:00"), end_time=dt("2026-10-18 09:40"),
                                clock_offset_seconds=1800),
                PositionSegment(vehicle_id="沪A-D2101", site_id="ROAD-S7",
                                start_time=dt("2026-10-18 10:05"), end_time=dt("2026-10-18 10:07"),
                                clock_offset_seconds=1800),
            ]
        )
        db.commit()
        departure.check_departure(db, b3, tolerance_minutes=10)  # consistent
        adjudicator.adjudicate(db, b3)
        compiler.compile_amounts(db, b3)        # 9.75×80 = 780.00
        publisher.publish(db, b3)
        ev(source_type="accounting_receipt", raw_text="账务回执 FV-20261031-002：确认金额 780.00 元",
           event_time=dt("2026-10-31 18:05"), site_id="FINANCE",
           payload={"voucher": "FV-20261031-002", "amount": 780.00, "ref": "PUB-PC-20261018-003-v1"})

        # ================ 场景④ 校准更正（计量事实转复审中） ================
        b4 = mk_batch("PC-20261020-004", "HT-2026-004", "沪C-D7710", "2026-10-20 09:55")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1020-04：西场固废",
               event_time=dt("2026-10-20 09:55"), site_id="WEST",
               payload={"task_no": "RW-1020-04", "plate": "沪C-D7710"})
        link(b4, e, "task")
        e = ev(source_type="weighbridge_reading", raw_text="西场地磅 WB-02：净重 22.00t",
               event_time=dt("2026-10-20 10:05"), device_id="WB-02", site_id="WEST",
               calibration_version=3, payload={"weight_tons": 22.00, "plate": "沪C-D7710"})
        link(b4, e, "weighing")
        e = ev(source_type="facility_receipt", raw_text="处理场 FAC-01 签收单：净重 21.90t",
               event_time=dt("2026-10-20 10:30"), device_id="FAC-01-SCALE", site_id="FAC-01",
               payload={"weight_tons": 21.90, "plate": "沪C-D7710"})
        link(b4, e, "receipt")
        adjudicator.adjudicate(db, b4)          # 地磅为主计量点：22.00 选定
        compiler.compile_amounts(db, b4)        # 800+10×95+2×110 = 1970.00
        publisher.publish(db, b4)               # v1 已发布（旧账务视图保留原依据）
        # 检定机构更正 WB-02 v3：实际偏差 +2% → 新计费视图暂停引用
        calibration_svc.correct_calibration(db, cal_wb02, factor=1.02, note="检定机构更正：v3 期间示值偏低 2%")
        publisher.publish(db, b4)               # v2 paused：复审中，暂停引用

        # ================ 场景⑤ 双批次争用（关系册冻结分配） ================
        b5a = mk_batch("PC-20261022-005A", "HT-2026-001", "沪D-D8801", "2026-10-22 10:00")
        b5b = mk_batch("PC-20261022-005B", "HT-2026-001", "沪E-D9902", "2026-10-22 10:05")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1022-05A",
               event_time=dt("2026-10-22 10:00"), site_id="EAST-LINE-7",
               payload={"task_no": "RW-1022-05A", "plate": "沪D-D8801"})
        link(b5a, e, "task")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1022-05B",
               event_time=dt("2026-10-22 10:05"), site_id="EAST-LINE-8",
               payload={"task_no": "RW-1022-05B", "plate": "沪E-D9902"})
        link(b5b, e, "task")
        shared = ev(source_type="weighbridge_reading", raw_text="转运站地磅 WB-03：净重 23.10t（车牌识别待核）",
                    event_time=dt("2026-10-22 10:12"), device_id="WB-03", site_id="TRANSFER",
                    calibration_version=1, payload={"weight_tons": 23.10, "plate": "待核"})
        registry.add_link(db, b5a, shared.id, "weighing")   # 先候选
        registry.add_link(db, b5b, shared.id, "weighing")   # 触发争用 → 双方冻结
        e = ev(source_type="facility_receipt", raw_text="处理场 FAC-01 签收单：净重 23.02t，车 沪D-D8801",
               event_time=dt("2026-10-22 10:40"), device_id="FAC-01-SCALE", site_id="FAC-01",
               payload={"weight_tons": 23.02, "plate": "沪D-D8801"})
        link(b5a, e, "receipt")

        # ================ 场景⑥ 跨阶梯区间（边界金额分列，不取中点） ================
        b6 = mk_batch("PC-20261025-006", "HT-2026-002", "沪F-D5555", "2026-10-25 11:00")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1025-06：跨区转运",
               event_time=dt("2026-10-25 11:00"), site_id="CROSS-2",
               payload={"task_no": "RW-1025-06", "plate": "沪F-D5555"})
        link(b6, e, "task")
        e = ev(source_type="weighbridge_reading", raw_text="东场地磅 WB-01：净重 9.98t",
               event_time=dt("2026-10-25 11:10"), device_id="WB-01", site_id="EAST",
               calibration_version=2, payload={"weight_tons": 9.98, "plate": "沪F-D5555"})
        link(b6, e, "weighing")
        e = ev(source_type="weighbridge_reading", raw_text="转运站地磅 WB-03：净重 10.05t",
               event_time=dt("2026-10-25 11:35"), device_id="WB-03", site_id="TRANSFER",
               calibration_version=1, payload={"weight_tons": 10.05, "plate": "沪F-D5555"})
        link(b6, e, "weighing")
        adjudicator.adjudicate(db, b6)          # 区间 [9.99975, 10.0299] 跨越 10t 阶梯
        compiler.compile_amounts(db, b6)        # 边界金额 799.98 / 802.84，amount 留空
        publisher.publish(db, b6)

        db.commit()
        print("[seed] 演示数据已写入：6 个验收场景批次、2 个费率版本、3 台地磅校准、2 个测试账号")
    finally:
        db.close()


if __name__ == "__main__":
    from app.db import init_db

    init_db()
    seed()
    from seed_sla import seed_sla

    seed_sla()
