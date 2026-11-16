"""领域模型。

计量域（原有）：来源证据库、批次关系册、计量裁定、费率编译、差异发布。
绩效域（SLA）：排班承诺、车辆到场、处理场签收、居民投诉、承运商申辩、主管裁定、
月度评分（双模式）与月结调整单。

设计要点：
- 证据（SourceEvent）、排班（ScheduledTask）与费率目录（RateCatalog）创建后不可变，
  内容哈希用于审计核对；业务状态流转只追加/更新状态字段，不删除原文。
- 计量事实 / 金额事实 / 发布记录为版本化追加写，历史版本永不回写。
- 月度评分同样版本化：增量模式每次证据到达产生新版本并记录受影响车次；
  月结锁定后迟到签收形成调整单，旧付款凭证不改写。
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 用户与会话


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    # analyst（数据团队，可写） / auditor（财务，只读） / supervisor（主管，裁定与锁定）
    role: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


# ---------------------------------------------------------------- 合同与费率


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 如 HT-2026-001
    name: Mapped[str] = mapped_column(String(128))
    contract_type: Mapped[str] = mapped_column(String(64))  # 主计量点合同 / 区间裁定合同
    strategy: Mapped[str] = mapped_column(String(16))  # priority / interval
    # 优先级策略：来源优先级列表，如 ["facility_receipt","weighbridge_reading","gate_record"]
    source_priority: Mapped[list] = mapped_column(JSON, default=list)
    # 质量门槛：事件时间与任务时间的容差（分钟）
    time_tolerance_minutes: Mapped[int] = mapped_column(Integer, default=45)
    # 区间策略：单份重量的质量容差（比例，如 0.005 表示 ±0.5%）
    quality_tolerance_pct: Mapped[float] = mapped_column(Float, default=0.005)
    # 已批准的换算依据（区间不重叠时可用），如 {"factor": 0.98, "note": "..."}
    approved_conversion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # ---- SLA 绩效域 ----
    # 只有已确认（confirmed）合同的服务任务才进入绩效评分草稿
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # SLA 条款：
    # {"on_time_arrival_minutes":10, "receipt_due_hours":24,
    #  "penalties":{"missed_collection":8,"late_arrival":3,"valid_complaint":5,"late_receipt":2},
    #  "complaint_merge_window_minutes":120, "complaint_radius_meters":300}
    sla_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateCatalog(Base):
    """费率目录：冻结后不可变；修订产生新版本，不回写历史。"""

    __tablename__ = "rate_catalogs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frozen: Mapped[bool] = mapped_column(Boolean, default=True)
    # 累进阶梯：[{"up_to": 10, "rate": 80.0}, {"up_to": 20, "rate": 95.0}, {"up_to": null, "rate": 110.0}]
    tiers: Mapped[list] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CalibrationRecord(Base):
    """地磅校准记录：更正产生新版本，旧版本标记 corrected，历史不删除。"""

    __tablename__ = "calibration_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    factor: Mapped[float] = mapped_column(Float, default=1.0)  # 真值 = 读数 × factor
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / corrected
    corrected_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- 来源证据库


class SourceEvent(Base):
    """来源证据：收运任务/门岗/地磅/签收/费率目录/账务回执的原文与元数据。"""

    __tablename__ = "source_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    # collection_task / gate_record / weighbridge_reading / facility_receipt /
    # rate_catalog / accounting_receipt
    raw_text: Mapped[str] = mapped_column(Text)  # 原文
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    site_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calibration_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # 结构化字段（重量、车牌等）
    content_hash: Mapped[str] = mapped_column(String(64), index=True)  # sha256
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PositionSegment(Base):
    """车辆定位片段：raw 时间为设备时钟，clock_offset_seconds 为设备快于真值的秒数。"""

    __tablename__ = "position_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String(32), index=True)
    site_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clock_offset_seconds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- 批次关系册


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 如 PC-20261012-001
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"))
    vehicle_id: Mapped[str] = mapped_column(String(32))
    service_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 任务服务时间
    status: Mapped[str] = mapped_column(String(24), default="open")
    # open / adjudicated / published / under_review
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    contract: Mapped[Contract] = relationship()


class BatchLink(Base):
    """批次与证据的关联：candidate → confirmed / rejected；争用时 frozen。"""

    __tablename__ = "batch_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("source_events.id"))
    link_type: Mapped[str] = mapped_column(String(16))  # task / weighing / receipt / gate
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    # candidate / confirmed / frozen / rejected
    reason: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    event: Mapped[SourceEvent] = relationship()


# ---------------------------------------------------------------- 计量裁定与金额事实


class MeasurementFact(Base):
    """计量裁定结果：计费重量或可接受区间、待核冲突、排除原因。版本化追加。"""

    __tablename__ = "measurement_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    strategy: Mapped[str] = mapped_column(String(16))  # priority / interval
    status: Mapped[str] = mapped_column(String(24), default="issued")
    # issued / pending_conflict / under_review
    billing_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    acceptable_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    acceptable_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discrepancy: Mapped[list] = mapped_column(JSON, default=list)  # 差异佐证
    conflicts: Mapped[list] = mapped_column(JSON, default=list)  # 待核冲突
    exclusions: Mapped[list] = mapped_column(JSON, default=list)  # 排除原因
    calibration_refs: Mapped[list] = mapped_column(JSON, default=list)  # 校准引用
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AmountFact(Base):
    """金额事实：在冻结费率目录上编译生成；解释版本递增，历史不回写。"""

    __tablename__ = "amount_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    measurement_fact_id: Mapped[int] = mapped_column(ForeignKey("measurement_facts.id"))
    interpretation_version: Mapped[int] = mapped_column(Integer)
    catalog_id: Mapped[int] = mapped_column(ForeignKey("rate_catalogs.id"))
    catalog_version: Mapped[int] = mapped_column(Integer)
    weight_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)  # 已确认金额
    amount_low: Mapped[float | None] = mapped_column(Float, nullable=True)  # 区间下界金额
    amount_high: Mapped[float | None] = mapped_column(Float, nullable=True)  # 区间上界金额
    cross_tier: Mapped[bool] = mapped_column(Boolean, default=False)  # 上下界跨越费用阶梯
    status: Mapped[str] = mapped_column(String(16), default="issued")  # issued / superseded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Publication(Base):
    """差异发布：计费依据、未决范围、下游引用版本。发布记录不可变。"""

    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    strategy: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default="published")
    # published / paused / pending
    billing_basis: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 计费依据
    pending_ranges: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 未决范围
    downstream_ref: Mapped[str] = mapped_column(String(64))  # 下游引用版本号
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DepartureCheck(Base):
    """签收后离场核查：比较支持到场的定位片段、时钟校正与容差。"""

    __tablename__ = "departure_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    result: Mapped[str] = mapped_column(String(16))  # consistent / contradiction / insufficient
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ================================================================
# SLA 绩效域：排班承诺、车辆到场、签收、投诉、申辩裁定、月度评分
# ================================================================


class ScheduledTask(Base):
    """排班承诺：合同窗口内对某个地址/车辆的收运承诺，是绩效评分的核算单位。"""

    __tablename__ = "scheduled_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 如 ST-20261103-01
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), index=True)
    vehicle_id: Mapped[str] = mapped_column(String(32), index=True)
    address: Mapped[str] = mapped_column(String(256))
    # 经纬度用于投诉按地址距离形成合并候选；可为空（仅按地址文本匹配）
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    scheduled_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # 评分资格：只有合同条款及证据支持的任务才进入评分
    # eligible（默认，已确认合同）/ excluded（合同未确认或证据排除，记录原因）
    status: Mapped[str] = mapped_column(String(16), default="eligible")
    exclude_reason: Mapped[str] = mapped_column(String(256), default="")
    # 合规判定快照（每次评分运行重算）：
    # {"on_time":bool,"missed":bool,"late_arrival":bool,"late_receipt":bool,
    #  "arrival_event_id","receipt_event_id","arrival_delay_minutes","receipt_delay_hours",
    #  "violations":["missed_collection",...],"evidence_complete":bool}
    compliance: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    contract: Mapped[Contract] = relationship()


class VehicleArrival(Base):
    """车辆到场证据（车载终端/门岗到场打卡），与排班任务按车+地址+时间窗匹配。"""

    __tablename__ = "vehicle_arrivals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String(32), index=True)
    address: Mapped[str] = mapped_column(String(256), index=True)
    arrival_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_ref: Mapped[str] = mapped_column(String(64), default="")  # 终端/门岗流水号
    raw_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("scheduled_tasks.id"), nullable=True, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FacilitySignoff(Base):
    """处理场签收证据：承诺到场后约定小时内须签收；迟到签收月结锁定后另出调整单。"""

    __tablename__ = "facility_signoffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("scheduled_tasks.id"), index=True)
    vehicle_id: Mapped[str] = mapped_column(String(32), index=True)
    signoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    site_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ref: Mapped[str] = mapped_column(String(64), default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    weight_tons: Mapped[float | None] = mapped_column(Float, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    # 迟到签收在锁定后到达：adjusted 时评分不再改写，只产生调整单
    adjusted: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ComplaintMergeGroup(Base):
    """投诉合并组：按地址、时间窗与事件关系形成的合并候选/确认结果。

    同一问题被多人报告时归入同一组，评分只按一组计一次罚分，不重复计罚。
    """

    __tablename__ = "complaint_merge_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # candidate（系统合并候选，待主管确认）/ confirmed（确认同一事件）/ dismissed（并非同一事件）
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    address: Mapped[str] = mapped_column(String(256))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    relation: Mapped[str] = mapped_column(String(32), default="")  # same_address / time_window / geo_radius
    decided_by: Mapped[str] = mapped_column(String(64), default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Complaint(Base):
    """居民投诉：未核投诉只列风险不扣分；核实后关联到真正受影响的收运任务才计罚。"""

    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reporter: Mapped[str] = mapped_column(String(64))  # 报告人（可重复，多人报告同一问题）
    address: Mapped[str] = mapped_column(String(256), index=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    category: Mapped[str] = mapped_column(String(48), default="")  # missed_collection / spillage / odor / other
    description: Mapped[str] = mapped_column(Text, default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    # unverified（默认，只列风险）/ verified（核实，关联任务后可计罚）/ dismissed（不成立）
    status: Mapped[str] = mapped_column(String(16), default="unverified", index=True)
    merge_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("complaint_merge_groups.id"), nullable=True, index=True
    )
    task_id: Mapped[str | None] = mapped_column(ForeignKey("scheduled_tasks.id"), nullable=True, index=True)
    verified_by: Mapped[str] = mapped_column(String(64), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verify_note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    merge_group: Mapped[ComplaintMergeGroup | None] = relationship()


class CarrierAppeal(Base):
    """承运商申辩：与主管裁定并列保存；申辩只改变对应任务的裁定状态。"""

    __tablename__ = "carrier_appeals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("scheduled_tasks.id"), index=True)
    carrier_ref: Mapped[str] = mapped_column(String(64), default="")  # 承运商申辩单号
    # 申辩针对的违约类型：missed_collection / late_arrival / late_receipt / valid_complaint
    violation_type: Mapped[str] = mapped_column(String(32), default="")
    reason: Mapped[str] = mapped_column(Text)
    evidence_ref: Mapped[str] = mapped_column(String(128), default="")
    # pending（待裁定）/ upheld（驳回申辩，违约成立）/ overturned（采纳申辩，撤销违约）
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupervisorRuling(Base):
    """主管裁定：与承运商申辩并列保存，逐条对应。"""

    __tablename__ = "supervisor_rulings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appeal_id: Mapped[int] = mapped_column(ForeignKey("carrier_appeals.id"), unique=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("scheduled_tasks.id"), index=True)
    # upheld（违约成立）/ overturned（违约撤销）
    outcome: Mapped[str] = mapped_column(String(16))
    ruling_note: Mapped[str] = mapped_column(Text, default="")
    ruled_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MonthlyScore(Base):
    """月度评分：按合同+服务月+模式维护；同一月份多次运行产生多个版本。"""

    __tablename__ = "monthly_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    mode: Mapped[str] = mapped_column(String(16))  # incremental / month_end
    version: Mapped[int] = mapped_column(Integer, default=1)
    # draft（草稿，可随证据增量更新）/ published（已发布）/ locked（月结锁定，稳定可复核）
    status: Mapped[str] = mapped_column(String(16), default="draft")
    score: Mapped[float] = mapped_column(Float, default=100.0)
    base_score: Mapped[float] = mapped_column(Float, default=100.0)
    total_deduction: Mapped[float] = mapped_column(Float, default=0.0)
    # 本次运行受影响车次（任务号），月内同分会多次变化时可逐版本追溯
    affected_task_ids: Mapped[list] = mapped_column(JSON, default=list)
    # 评分明细（按任务）：违约类型、罚分、投诉/申辩/裁定引用
    details: Mapped[list] = mapped_column(JSON, default=list)
    # 风险项（未核投诉、证据缺口等不扣分但需关注的事项）
    risks: Mapped[list] = mapped_column(JSON, default=list)
    evidence_batch_ref: Mapped[str] = mapped_column(String(64), default="")  # 月结证据批次号
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str] = mapped_column(String(64), default="")
    # 锁定后的付款状态：已支付罚款的追溯变更只走调整记录
    payment_status: Mapped[str] = mapped_column(String(16), default="unpaid")  # unpaid / paid
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 申辩结论与评分发布并发：主管需重新查看影响车次
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    contract: Mapped[Contract] = relationship()


class ScoreVersion(Base):
    """评分版本流水：每次运行追加一条不可变记录，保存当时快照与受影响车次。"""

    __tablename__ = "score_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    score_id: Mapped[int] = mapped_column(ForeignKey("monthly_scores.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    total_deduction: Mapped[float] = mapped_column(Float)
    affected_task_ids: Mapped[list] = mapped_column(JSON, default=list)
    change_note: Mapped[str] = mapped_column(String(512), default="")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LockAdjustment(Base):
    """月结调整单：锁定后迟到签收/追溯裁定产生；旧付款不改写，只追加调整。"""

    __tablename__ = "lock_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    score_id: Mapped[int] = mapped_column(ForeignKey("monthly_scores.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("scheduled_tasks.id"), index=True)
    adjustment_type: Mapped[str] = mapped_column(String(32))
    # late_signoff_after_lock / ruling_after_lock / complaint_verified_after_lock
    delta_deduction: Mapped[float] = mapped_column(Float, default=0.0)  # 正=追加扣罚，负=返还
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence_ref: Mapped[str] = mapped_column(String(128), default="")
    # open（待处理）/ applied（已形成调整版本，原凭证不动）
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
