"""日志检索工具（方向一·任务2）：查 Loki。

用 LogQL 查最近一段时间的日志。选择器用 service_name 标签（promtail 采集容器日志时，Loki 会把
容器名同时映射成 container 和 service_name，取值一致，这里统一用 service_name）。级别和关键词
都用行过滤（|~ 正则，大小写不敏感）实现，不依赖 Loki 3.x 的 detected_level 自动识别——换一个
没开该特性的 Loki 也能用。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from langchain_core.tools import tool

from app.services.observability_service import loki_label_values, loki_query_range

# 单次最多返回多少行（同时作为 Loki 的 limit）。日志量可能很大，做截断保护。
_LINE_LIMIT = 200
# 单行最长展示多少字符，超长截断——防超长行，也顺带收敛日志里可能夹带的 prompt 注入内容。
_MAX_LINE_LEN = 500


@tool
async def search_logs(
    service: str = "",
    keyword: str = "",
    level: str = "",
    minutes: int = 60,
) -> str:
    """搜索最近 N 分钟的日志，数据来自 Loki。

    - service：按服务名过滤（如 "aioms-fake-app"）。不传则查所有服务的日志。
    - keyword：只保留包含该关键词的行（大小写不敏感）。
    - level：只看某个级别，取 error / warn / info 之一（按日志文本匹配）。
    - minutes：往前查多少分钟，默认 60。
    """
    # 1. 选择器：指定 service 就精确匹配，否则匹配所有非空 service_name
    if service:
        selector = f'{{service_name="{_escape_label(service)}"}}'
    else:
        selector = '{service_name=~".+"}'

    # 2. 行过滤：级别 + 关键词都用 |~ 正则（大小写不敏感）。关键词转义，防用户输入里的正则元字符。
    logql = selector
    if level:
        logql += f' |~ "(?i){re.escape(level)}"'
    if keyword:
        logql += f' |~ "(?i){re.escape(keyword)}"'

    # 3. 时间窗：最近 minutes 分钟。Loki 时间戳用纳秒。查询边界用带时区的 UTC（正确），
    #    展示时再转本地时间（对用户直观）。
    end_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    start_ns = end_ns - minutes * 60 * 10**9

    try:
        streams = await loki_query_range(logql, start_ns, end_ns, limit=_LINE_LIMIT)
    except Exception as exc:  # noqa: BLE001 —— 网络/超时/查询语法错都转成给 LLM 读的文本
        return (
            f"查询 Loki 失败：{exc}。"
            "请确认 Loki 在跑（默认 http://localhost:3100），以及查询条件是否合理。"
        )

    # 4. 跨 stream 展平成 (纳秒时间戳, 服务名, 行)，按时间倒序。Loki 每个 stream 内有序，
    #    但多个 stream（比如按 detected_level 拆开的）之间不保证有序，必须重排。
    rows: list[tuple[int, str, str]] = []
    for s in streams:
        labels = s.get("stream", {})
        svc = labels.get("service_name") or labels.get("container") or "?"
        for ts, line in s.get("values", []):
            rows.append((int(ts), svc, line))
    rows.sort(key=lambda r: r[0], reverse=True)

    if not rows:
        parts = [f"最近 {minutes} 分钟", f"服务 {service}" if service else "所有服务"]
        if level:
            parts.append(f"级别 {level}")
        if keyword:
            parts.append(f'含 "{keyword}"')
        return "、".join(parts) + " 没有匹配的日志。"

    truncated = len(rows) >= _LINE_LIMIT
    rows = rows[:_LINE_LIMIT]

    # 5. 格式化。查多个服务时每行带上服务名；只查单个服务时不重复带。
    show_svc = not service
    lines: list[str] = []
    for ts, svc, line in rows:
        clock = datetime.fromtimestamp(ts / 1e9).strftime("%H:%M:%S")  # 本地时间，直观
        text = _clean(line)
        lines.append(f"[{clock}] {svc} | {text}" if show_svc else f"[{clock}] {text}")

    scope = f"服务 {service}" if service else "所有服务"
    header = f"{scope}最近 {minutes} 分钟日志（按时间倒序，来自 Loki）："
    if truncated:
        header += f"\n（命中较多，只显示最近 {_LINE_LIMIT} 条）"
    return header + "\n" + "\n".join(lines)


@tool
async def list_log_services() -> str:
    """列出当前 Loki 里有日志可查的服务名（给 search_logs 的 service 参数选用）。

    不确定要查哪个服务、或用户报的名字拿不准时，先调这个看有哪些服务，再去 search_logs 查。
    """
    try:
        services = await loki_label_values("service_name")
    except Exception as exc:  # noqa: BLE001 —— 转成给 LLM 读的文本
        return (
            f"查询 Loki 服务列表失败：{exc}。"
            "请确认 Loki 在跑（默认 http://localhost:3100）。"
        )
    if not services:
        return "Loki 里当前没有任何服务的日志（可能采集还没上来，或近期窗口内无日志）。"
    lines = "\n".join(f"- {s}" for s in sorted(services))
    return "当前 Loki 里有日志可查的服务（可作为 search_logs 的 service 参数）：\n" + lines


def _escape_label(value: str) -> str:
    """转义标签值里的反斜杠/双引号，避免拼进 {service_name="..."} 时破坏 LogQL。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _clean(line: str) -> str:
    """给日志行做最基本的收敛：压掉换行/回车、截断超长行。日志内容属于不可信输入
    （可能夹带 prompt 注入），这里只做展示层的轻处理，真正的隔离靠 PERSONA 的铁律。"""
    line = line.replace("\r", " ").replace("\n", " ")
    if len(line) > _MAX_LINE_LEN:
        line = line[:_MAX_LINE_LEN] + "…（已截断）"
    return line
