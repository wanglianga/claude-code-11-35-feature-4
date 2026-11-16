"""测试基座：使用独立的 ccdev_test 数据库，每个用例前清空所有表。"""

import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

TEST_URL = "postgresql+psycopg2://ccdev:ccdev@127.0.0.1:5432/ccdev_test"
os.environ["DATABASE_URL"] = TEST_URL
os.environ["SEED_DEMO"] = "false"

_conn = psycopg2.connect(host="127.0.0.1", user="ccdev", password="ccdev", dbname="ccdev")
_conn.autocommit = True
with _conn.cursor() as cur:
    cur.execute("SELECT 1 FROM pg_database WHERE datname='ccdev_test'")
    if not cur.fetchone():
        cur.execute("CREATE DATABASE ccdev_test")
_conn.close()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Batch,
    CalibrationRecord,
    Contract,
    PositionSegment,
    RateCatalog,
    User,
)
from app.security import make_password_hash  # noqa: E402
from app.services import evidence, registry  # noqa: E402

CST = timezone(timedelta(hours=8))
init_db()


def dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=CST)


@pytest.fixture()
def db():
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------- 构造助手

TIERS_V1 = [{"up_to": 10, "rate": 80.0}, {"up_to": 20, "rate": 95.0}, {"up_to": None, "rate": 110.0}]


def make_users(db):
    db.add_all(
        [
            User(username="analyst", password_hash=make_password_hash("pw123456"),
                 role="analyst", display_name="分析员"),
            User(username="auditor", password_hash=make_password_hash("pw123456"),
                 role="auditor", display_name="复核员"),
        ]
    )
    db.commit()


def make_contract(db, cid="HT-T-001", strategy="priority", priority=None, tol_min=45, qtol=0.005, conv=None):
    c = Contract(
        id=cid,
        name=f"测试合同 {cid}",
        contract_type="主计量点合同" if strategy == "priority" else "区间裁定合同",
        strategy=strategy,
        source_priority=priority or ["facility_receipt", "weighbridge_reading", "gate_record"],
        time_tolerance_minutes=tol_min,
        quality_tolerance_pct=qtol,
        approved_conversion=conv,
    )
    db.add(c)
    db.commit()
    return c


def make_catalog(db, version=1, effective="2026-01-01 00:00", effective_to=None, tiers=None):
    c = RateCatalog(
        version=version,
        effective_from=dt(effective),
        effective_to=dt(effective_to) if effective_to else None,
        frozen=True,
        tiers=tiers or TIERS_V1,
        note=f"目录 v{version}",
    )
    db.add(c)
    db.commit()
    return c


def make_calibration(db, device="WB-01", version=2, factor=1.0):
    cal = CalibrationRecord(
        device_id=device, version=version, effective_from=dt("2026-01-01 00:00"),
        factor=factor, status="active",
    )
    db.add(cal)
    db.commit()
    return cal


def make_batch(db, bid="PC-T-001", cid="HT-T-001", vehicle="沪A-T001", when="2026-10-12 08:55"):
    b = Batch(id=bid, contract_id=cid, vehicle_id=vehicle, service_time=dt(when))
    db.add(b)
    db.commit()
    return b


def add_event(db, source_type, weight=None, when="2026-10-12 09:00", device=None,
              cal=None, site="EAST", plate="沪A-T001"):
    payload = {"plate": plate}
    if weight is not None:
        payload["weight_tons"] = weight
    return evidence.ingest(
        db,
        source_type=source_type,
        raw_text=f"{source_type} 原文 @{when}",
        event_time=dt(when),
        device_id=device,
        site_id=site,
        calibration_version=cal,
        payload=payload,
    )


def link_confirmed(db, batch, event, ltype):
    link = registry.add_link(db, batch, event.id, ltype)
    if link.status == "candidate":
        link = registry.confirm_link(db, link, "测试确认")
    return link


def add_position(db, vehicle, site, start, end, offset=0):
    seg = PositionSegment(
        vehicle_id=vehicle, site_id=site,
        start_time=dt(start), end_time=dt(end), clock_offset_seconds=offset,
    )
    db.add(seg)
    db.commit()
    return seg
