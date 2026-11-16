from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import CalibrationRecord, Contract, RateCatalog, User
from ..schemas import CalibrationCorrectIn, CatalogIn
from ..security import get_current_user, require_analyst
from ..services import calibration as calibration_svc
from ..services import evidence as evidence_svc

router = APIRouter(prefix="/api", tags=["catalogs"])


@router.get("/contracts")
def list_contracts(db: OrmSession = Depends(get_db), _: User = Depends(get_current_user)):
    return [
        {
            "id": c.id,
            "name": c.name,
            "contract_type": c.contract_type,
            "strategy": c.strategy,
            "source_priority": c.source_priority,
            "time_tolerance_minutes": c.time_tolerance_minutes,
            "quality_tolerance_pct": c.quality_tolerance_pct,
            "approved_conversion": c.approved_conversion,
            "confirmed": c.confirmed,
            "confirmed_at": c.confirmed_at.isoformat() if c.confirmed_at else None,
            "sla_rules": c.sla_rules,
        }
        for c in db.query(Contract).order_by(Contract.id).all()
    ]


@router.get("/rate-catalogs")
def list_catalogs(db: OrmSession = Depends(get_db), _: User = Depends(get_current_user)):
    return [
        {
            "id": c.id,
            "version": c.version,
            "effective_from": c.effective_from.isoformat(),
            "effective_to": c.effective_to.isoformat() if c.effective_to else None,
            "frozen": c.frozen,
            "tiers": c.tiers,
            "note": c.note,
        }
        for c in db.query(RateCatalog).order_by(RateCatalog.version).all()
    ]


@router.post("/rate-catalogs", status_code=201)
def create_catalog(
    body: CatalogIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    """新增目录版本：自动冻结并衔接上一版本有效期；历史版本不回写。"""
    latest = db.query(RateCatalog).order_by(RateCatalog.version.desc()).first()
    if latest and body.effective_from <= latest.effective_from:
        raise HTTPException(409, "新目录生效时间必须晚于现有最新版本")
    if latest and latest.effective_to is None:
        latest.effective_to = body.effective_from
    catalog = RateCatalog(
        version=(latest.version + 1) if latest else 1,
        effective_from=body.effective_from,
        frozen=True,
        tiers=body.tiers,
        note=body.note,
    )
    db.add(catalog)
    db.commit()
    db.refresh(catalog)
    # 目录原文同步入来源证据库，保证费率依据可哈希核对
    evidence_svc.ingest(
        db,
        source_type="rate_catalog",
        raw_text=f"费率目录 v{catalog.version}：{body.note}；阶梯 {body.tiers}",
        event_time=body.effective_from,
        site_id="RATE-CENTER",
        payload={"catalog_version": catalog.version, "tiers": body.tiers},
    )
    return {"id": catalog.id, "version": catalog.version, "frozen": catalog.frozen}


@router.get("/calibrations")
def list_calibrations(db: OrmSession = Depends(get_db), _: User = Depends(get_current_user)):
    return [
        {
            "id": c.id,
            "device_id": c.device_id,
            "version": c.version,
            "effective_from": c.effective_from.isoformat(),
            "factor": c.factor,
            "status": c.status,
            "corrected_by_id": c.corrected_by_id,
            "note": c.note,
        }
        for c in db.query(CalibrationRecord).order_by(CalibrationRecord.device_id, CalibrationRecord.version).all()
    ]


@router.post("/calibrations/{cal_id}/correct", status_code=201)
def correct_calibration(
    cal_id: int,
    body: CalibrationCorrectIn,
    db: OrmSession = Depends(get_db),
    _: User = Depends(require_analyst),
):
    cal = db.get(CalibrationRecord, cal_id)
    if cal is None:
        raise HTTPException(404, "校准记录不存在")
    successor, affected = calibration_svc.correct_calibration(db, cal, body.factor, body.note)
    return {
        "successor": {
            "id": successor.id,
            "device_id": successor.device_id,
            "version": successor.version,
            "factor": successor.factor,
            "status": successor.status,
        },
        "affected_batches": affected,
        "message": "受影响计量事实已转为复审中；新计费视图暂停引用，历史账务视图保留原依据",
    }
