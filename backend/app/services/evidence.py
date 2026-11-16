"""来源证据库：原文、事件时间、设备/场地标识、校准版本与内容哈希。"""

import hashlib
import json
from datetime import timezone

from sqlalchemy.orm import Session

from ..models import SourceEvent


def compute_hash(source_type, raw_text, event_time, device_id, site_id, calibration_version, payload) -> str:
    # 事件时间统一归一化到 UTC，保证入库前后重算结果一致（内容寻址）
    if event_time.tzinfo is not None:
        event_time = event_time.astimezone(timezone.utc)
    canonical = json.dumps(
        {
            "source_type": source_type,
            "raw_text": raw_text,
            "event_time": event_time.isoformat(),
            "device_id": device_id,
            "site_id": site_id,
            "calibration_version": calibration_version,
            "payload": payload,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ingest(db: Session, *, source_type, raw_text, event_time, device_id=None, site_id=None,
           calibration_version=None, payload=None) -> SourceEvent:
    payload = payload or {}
    event = SourceEvent(
        source_type=source_type,
        raw_text=raw_text,
        event_time=event_time,
        device_id=device_id,
        site_id=site_id,
        calibration_version=calibration_version,
        payload=payload,
        content_hash=compute_hash(
            source_type, raw_text, event_time, device_id, site_id, calibration_version, payload
        ),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def to_dict(e: SourceEvent) -> dict:
    return {
        "id": e.id,
        "source_type": e.source_type,
        "raw_text": e.raw_text,
        "event_time": e.event_time.isoformat(),
        "device_id": e.device_id,
        "site_id": e.site_id,
        "calibration_version": e.calibration_version,
        "payload": e.payload,
        "content_hash": e.content_hash,
        "received_at": e.received_at.isoformat(),
    }
