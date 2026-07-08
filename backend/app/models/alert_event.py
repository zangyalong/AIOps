from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class AlertEvent(Base, UUIDMixin, TimestampMixin):
    """运维告警事件（方向二）。由 Alertmanager 通过 webhook 推送进来，落这张表。

    按 fingerprint（Alertmanager 给的稳定去重键）唯一：同一条告警反复 firing/resolved 时更新同一行，
    而不是插一堆重复记录——这也为后续「事件生命周期状态机」（firing → acked → resolved）打底。
    created_at = 第一次收到的时间，updated_at = 最近一次更新的时间（都来自 TimestampMixin）。
    """

    __tablename__ = "alert_events"

    # Alertmanager 的稳定指纹：同一组 label 恒为同一值。唯一约束 + 索引，upsert 就靠它。
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    alertname: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # critical / warning / info
    # firing / resolved。后续状态机会再加 acked（谁认领了）等。
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # annotations.summary
    labels: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 全部标签，留着灵活查
    # 从 labels 里抽出来的常用维度，方便直接过滤 / 和任务5 的实体（monitor_labels.instance）对齐。
    instance: Mapped[str | None] = mapped_column(String(255), nullable=True)

    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 可空，先不自动解析——以后可按 instance 反查 servers.monitor_labels 关联到具体服务器。
    server_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("servers.id", ondelete="SET NULL"), nullable=True
    )

    # 自动诊断（方向二 triage 回填）：agent 后台调查后写回的诊断结论 + 诊断时刻。
    diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
