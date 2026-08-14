"""日志检索工具（方向一·任务2）：查 Loki。

新 Alloy 节点使用 ``service`` 作为统一身份标签，并在迁移期同时写 ``service_name``。这里兼容
旧 ``service_name`` 以及只有 ``container`` 标签的历史流，合并时按完整标签、时间戳和
日志正文去重。级别和关键词都用行过滤实现，不依赖 detected_level 自动识别。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from langchain_core.tools import tool

from app.services.observability_service import loki_label_values, loki_query_range

# 单次最多返回多少行（同时作为 Loki 的 limit）。日志量可能很大，做截断保护。
_LINE_LIMIT = 200
# 单行最长展示多少字符，超长截断——防超长行，也顺带收敛日志里可能夹带的 prompt 注入内容。
_MAX_LINE_LEN = 500
_REGEX_META = frozenset(r"\.+*?()|[]{}^$")


@tool
async def search_logs(
    server: str = "",
    service: str = "",
    keyword: str = "",
    level: str = "",
    minutes: int = 60,
) -> str:
    """搜索最近 N 分钟的日志，数据来自 Loki。

    - server：按 AIOps 服务器标签过滤（如 "web-01"）。
    - service：按服务名过滤（如 "orders-api"）。不传则查该服务器所有服务的日志。
    - keyword：只保留包含该关键词的行（大小写不敏感）。
    - level：只看某个级别，取 error / warn / info 之一（按日志文本匹配）。
    - minutes：往前查多少分钟，默认 60。
    """
    # 1. 同时查新旧三种服务标签。Alloy 迁移期会让同一条日志命中 service/service_name，
    # 展平时按完整 stream 标签 + 时间戳 + 正文去重。
    if service:
        escaped_service = _escape_label(service)
        selectors = [
            _selector(server, "service", escaped_service, regex=False),
            _selector(server, "service_name", escaped_service, regex=False),
            _selector(server, "container", escaped_service, regex=False),
        ]
    else:
        selectors = [
            _selector(server, "service", ".+", regex=True),
            _selector(server, "service_name", ".+", regex=True),
            _selector(server, "container", ".+", regex=True),
        ]

    # 2. 行过滤：级别 + 关键词都用 |~ 正则（大小写不敏感）。关键词转义，防用户输入里的正则元字符。
    suffix = ""
    if level:
        suffix += f' |~ "{_escape_logql_string(f"(?i){_escape_regex_literal(level)}")}"'
    if keyword:
        suffix += (
            f' |~ "{_escape_logql_string(f"(?i){_escape_regex_literal(keyword)}")}"'
        )
    logql_queries = [selector + suffix for selector in selectors]

    # 3. 时间窗：最近 minutes 分钟。Loki 时间戳用纳秒。查询边界用带时区的 UTC（正确），
    #    展示时再转本地时间（对用户直观）。
    end_ns = int(datetime.now(UTC).timestamp() * 1e9)
    start_ns = end_ns - minutes * 60 * 10**9

    try:
        stream_groups = await asyncio.gather(
            *(
                loki_query_range(logql, start_ns, end_ns, limit=_LINE_LIMIT)
                for logql in logql_queries
            )
        )
    except Exception as exc:  # noqa: BLE001 —— 网络/超时/查询语法错都转成给 LLM 读的文本
        return (
            f"查询 Loki 失败：{exc}。"
            "请确认 Loki 在跑（默认 http://localhost:3100），以及查询条件是否合理。"
        )

    # 4. 跨 stream 展平成 (纳秒时间戳, 服务名, 行)，按时间倒序。Loki 每个 stream 内有序，
    #    但多个 stream（比如按 detected_level 拆开的）之间不保证有序，必须重排。
    rows: list[tuple[int, str, str]] = []
    seen_rows: set[tuple[int, str, tuple[tuple[str, str], ...]]] = set()
    for streams in stream_groups:
        for stream in streams:
            labels = stream.get("stream", {})
            svc = (
                labels.get("service")
                or labels.get("service_name")
                or labels.get("container")
                or "?"
            )
            label_key = tuple(
                sorted((str(key), str(value)) for key, value in labels.items())
            )
            for ts, line in stream.get("values", []):
                timestamp = int(ts)
                dedup_key = (timestamp, line, label_key)
                if dedup_key in seen_rows:
                    continue
                seen_rows.add(dedup_key)
                rows.append((timestamp, svc, line))
    rows.sort(key=lambda r: r[0], reverse=True)

    if not rows:
        parts = [f"最近 {minutes} 分钟"]
        if server:
            parts.append(f"服务器 {server}")
        parts.append(f"服务 {service}" if service else "所有服务")
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
        clock = (
            datetime.fromtimestamp(ts / 1e9, tz=UTC).astimezone().strftime("%H:%M:%S")
        )
        text = _clean(line)
        lines.append(f"[{clock}] {svc} | {text}" if show_svc else f"[{clock}] {text}")

    scope = f"服务 {service}" if service else "所有服务"
    if server:
        scope = f"服务器 {server} 的{scope}"
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
        services = await discover_log_services()
    except Exception as exc:  # noqa: BLE001 —— 转成给 LLM 读的文本
        return (
            f"查询 Loki 服务列表失败：{exc}。"
            "请确认 Loki 在跑（默认 http://localhost:3100）。"
        )
    if not services:
        return "Loki 里当前没有任何服务的日志（可能采集还没上来，或近期窗口内无日志）。"
    lines = "\n".join(f"- {s}" for s in sorted(services))
    return (
        "当前 Loki 里有日志可查的服务（可作为 search_logs 的 service 参数）：\n" + lines
    )


async def discover_log_services() -> set[str]:
    """读取 Loki 的真实服务标签，供工具展示和统一调查入口消歧。"""
    label_groups = await asyncio.gather(
        loki_label_values("service"),
        loki_label_values("service_name"),
        loki_label_values("container"),
    )
    return {value for group in label_groups for value in group if value}


def _escape_label(value: str) -> str:
    """转义标签值里的反斜杠/双引号，避免拼进 LogQL 选择器时破坏语法。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _escape_logql_string(value: str) -> str:
    """把正则写入 LogQL 双引号字符串；正则反斜杠必须再经过一层字符串转义。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _escape_regex_literal(value: str) -> str:
    """按 Go/RE2 QuoteMeta 的字符集合转义，避免 Python re.escape 过度转义连字符。"""
    return "".join(f"\\{char}" if char in _REGEX_META else char for char in value)


def _selector(server: str, label: str, value: str, *, regex: bool) -> str:
    match = "=~" if regex else "="
    labels = []
    if server:
        labels.append(f'server="{_escape_label(server)}"')
    labels.append(f'{label}{match}"{value}"')
    return "{" + ",".join(labels) + "}"


def _clean(line: str) -> str:
    """给日志行做最基本的收敛：压掉换行/回车、截断超长行。日志内容属于不可信输入
    （可能夹带 prompt 注入），这里只做展示层的轻处理，真正的隔离靠 PERSONA 的铁律。"""
    line = line.replace("\r", " ").replace("\n", " ")
    if len(line) > _MAX_LINE_LEN:
        line = line[:_MAX_LINE_LEN] + "…（已截断）"
    return line
