from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin

# 事件生命周期状态（方向二·任务②）
FIRING = "firing"
ACKNOWLEDGED = "acknowledged"
RESOLVED = "resolved"


class Incident(Base, UUIDMixin, TimestampMixin):
    """运维事件（方向二·任务②）：一组相关告警（同 group_key）聚成的一等实体，带生命周期状态机。

    告警是 Alertmanager 自动 firing/resolved 的原始信号；事件是在其上做关联/去重后、供人管理的单位
    （谁认领、诊断结论、什么时候恢复）。alert_events 通过 incident_id 挂到事件上。

    同一个 group_key 在时间上可能对应多个事件（这次烧起→恢复，下次又烧起是新事件）；但任一时刻
    每个 group_key 最多一个「未 resolved」的活跃事件。
    """

    __tablename__ = "incidents"

    group_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # firing / acknowledged / resolved（见本模块常量）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=FIRING)

    # 代表信息（来自触发这个事件的第一条告警），用于列表展示
    alertname: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    instance: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 自动诊断（triage 回填，现在挂在「事件」级而不是单条告警上）
    diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)
    triaged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 生命周期：谁认领的、何时认领、何时恢复。acked_by 现在是自由文本（还没接鉴权，方向三再接真身份）。
    acked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
