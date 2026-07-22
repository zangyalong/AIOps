from __future__ import annotations

from datetime import datetime, timedelta

from langchain_core.tools import tool

from app.db.session import AsyncSessionLocal
from app.models.change_record import ChangeRecord
from app.services import change_record_service

CHANGE_TYPE_LABELS = {
    "deployment": "发布",
    "rollback": "回滚",
    "scaling": "扩缩容",
    "configuration": "配置变更",
}


@tool
async def query_recent_changes(
    service: str = "",
    environment: str = "",
    at: str = "",
    window_minutes: int = 30,
) -> str:
    """查询指定时刻前后发生的发布、回滚、扩缩容和配置变更，为故障诊断补充变更证据。

    - service：服务名；留空查询所有服务。
    - environment：环境名；留空查询全部环境。
    - at：时间锚点 ISO8601；留空表示现在。
    - window_minutes：锚点前后各查多少分钟，默认 30。
    """
    if window_minutes <= 0 or window_minutes > 1440:
        return "window_minutes 必须在 1 到 1440 之间。"
    try:
        anchor = _parse_at(at)
    except ValueError:
        return "at 必须是 ISO8601 时间，例如 2026-07-22T14:05:00+08:00。"
    start = anchor - timedelta(minutes=window_minutes)
    end = anchor + timedelta(minutes=window_minutes)
    async with AsyncSessionLocal() as db:
        records = await change_record_service.list_change_records(
            db,
            service_name=service.strip() or None,
            environment=environment.strip() or None,
            start_at=start,
            end_at=end,
            limit=100,
        )
    heading = (
        f"变更查询窗口：{_clock(start)} ~ {_clock(end)}；"
        f"服务={service.strip() or '全部'}；环境={environment.strip() or '全部'}"
    )
    return f"{heading}\n{format_change_records(records)}"


def format_change_records(records: list[ChangeRecord]) -> str:
    if not records:
        return "窗口内没有记录到变更。"
    lines: list[str] = []
    for record in records:
        kind = CHANGE_TYPE_LABELS.get(record.change_type, record.change_type)
        identities = [value for value in (record.version, record.commit_hash) if value]
        identity = " · ".join(identities) or "未填写版本/commit"
        detail = f"；{record.description}" if record.description else ""
        lines.append(
            f"- {_clock(record.occurred_at)} [{kind}] "
            f"{record.service_name}/{record.environment} {identity}{detail}"
        )
    return "\n".join(lines)


def _parse_at(value: str) -> datetime:
    if not value.strip():
        return datetime.now().astimezone()
    parsed = datetime.fromisoformat(value.strip())
    return parsed.astimezone()


def _clock(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
