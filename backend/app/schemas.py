"""API 入参模型（出参直接由服务层组装为 dict，保证审计字段完整透传）。"""

from datetime import datetime

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str
    password: str


class EvidenceIn(BaseModel):
    source_type: str = Field(pattern="^(collection_task|gate_record|weighbridge_reading|facility_receipt|rate_catalog|accounting_receipt)$")
    raw_text: str
    event_time: datetime
    device_id: str | None = None
    site_id: str | None = None
    calibration_version: int | None = None
    payload: dict = Field(default_factory=dict)


class BatchIn(BaseModel):
    id: str
    contract_id: str
    vehicle_id: str
    service_time: datetime


class LinkIn(BaseModel):
    event_id: int
    link_type: str = Field(pattern="^(task|weighing|receipt|gate)$")


class LinkDecisionIn(BaseModel):
    reason: str = ""


class CatalogIn(BaseModel):
    effective_from: datetime
    tiers: list[dict]  # [{"up_to": 10|null, "rate": 80.0}, ...]
    note: str = ""


class CalibrationCorrectIn(BaseModel):
    factor: float
    note: str = ""


class DepartureCheckIn(BaseModel):
    tolerance_minutes: int = 10


# ---------------------------------------------------------------- SLA 绩效域


class SlaRulesIn(BaseModel):
    on_time_arrival_minutes: int = 10
    receipt_due_hours: int = 24
    complaint_merge_window_minutes: int = 120
    complaint_radius_meters: int = 300
    penalties: dict = Field(default_factory=dict)


class ContractConfirmIn(BaseModel):
    sla_rules: SlaRulesIn


class ScheduledTaskIn(BaseModel):
    id: str
    contract_id: str
    vehicle_id: str
    address: str
    scheduled_time: datetime
    latitude: float | None = None
    longitude: float | None = None


class VehicleArrivalIn(BaseModel):
    vehicle_id: str
    address: str
    arrival_time: datetime
    source_ref: str = ""
    raw_text: str = ""


class FacilitySignoffIn(BaseModel):
    task_id: str
    vehicle_id: str
    signoff_time: datetime
    site_id: str | None = None
    source_ref: str = ""
    raw_text: str = ""
    weight_tons: float | None = None


class ComplaintIn(BaseModel):
    reporter: str
    address: str
    event_time: datetime
    category: str = ""
    description: str = ""
    raw_text: str = ""
    latitude: float | None = None
    longitude: float | None = None


class MergeDecisionIn(BaseModel):
    confirm: bool
    reason: str = ""


class ComplaintVerifyIn(BaseModel):
    task_id: str | None = None
    note: str = ""
    dismiss: bool = False


class AppealIn(BaseModel):
    task_id: str
    violation_type: str = Field(
        pattern="^(missed_collection|late_arrival|late_receipt|valid_complaint)$"
    )
    reason: str
    carrier_ref: str = ""
    evidence_ref: str = ""


class RulingIn(BaseModel):
    outcome: str = Field(pattern="^(upheld|overturned)$")
    ruling_note: str = ""


class ScoringRunIn(BaseModel):
    contract_id: str
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    mode: str = Field(pattern="^(incremental|month_end)$")
    change_note: str = ""


class ScoreLockIn(BaseModel):
    contract_id: str
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    mode: str = Field(pattern="^(incremental|month_end)$")
    evidence_batch_ref: str = ""


class ScoreRefIn(BaseModel):
    contract_id: str
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    mode: str = Field(pattern="^(incremental|month_end)$")
