"""可观测性数据源（Prometheus/Loki/Alertmanager）的公共 HTTP 访问层。

这些数据源的调用本质都是"发一个 HTTP 请求 + 解析固定结构的 JSON"，没必要为每个上一整套
客户端库 / provider 框架。这里只抽真正共用的东西：一个进程级复用的 httpx.AsyncClient
（复用连接池），以及各数据源信封校验的小函数。每个工具各自拼自己的查询、格式化自己的结果，
不在这里耦合。

现在有 Prometheus（任务1）、Loki（任务2）；Alertmanager（任务3）用到时再往这里加对应的
请求函数，复用同一个 client。
"""

from __future__ import annotations

import httpx

from app.core.config import settings

# 进程级共享 client：复用连接池，避免每次工具调用都新建 TCP 连接。生命周期跟着进程走，
# 不手动 close（进程退出时 OS 回收），对本项目足够。
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # trust_env=False：不读环境里的 HTTP_PROXY/HTTPS_PROXY。这些数据源都是内网/本地
        # 可观测性端点（localhost:9090 等），永远应该直连，绝不该走外网代理。若启动后端的
        # 环境设了代理却没配 NO_PROXY=localhost，httpx 默认会把 localhost 请求也塞给代理，
        # 导致"连不上 Prometheus"——设 False 直接绕开这个坑，不依赖启动环境是否配对了 NO_PROXY。
        _client = httpx.AsyncClient(timeout=settings.obs_http_timeout, trust_env=False)
    return _client


async def prom_query(promql: str) -> list[dict]:
    """打 Prometheus 瞬时查询 /api/v1/query，返回 vector 结果列表（每项含 metric 标签 + value）。

    - 用 POST + 表单编码：PromQL 可能较长，避免 GET 的 URL 长度限制。
    - Prometheus 对语法错误返回 400 + JSON 错误体（比 HTTP 状态码有用得多），所以先解析
      JSON 信封、按 status 字段判断成败，再抛带原因的 RuntimeError，让工具能把错因转给 LLM。
    - 只处理 vector（瞬时向量）结果；其它 resultType（scalar/matrix/string）返回空列表，
      本工具的模板查询都是 vector，遇到别的说明表达式写法不适合瞬时查询。
    """
    resp = await get_client().post(
        f"{settings.prometheus_base_url}/api/v1/query",
        data={"query": promql},
    )
    try:
        body = resp.json()
    except ValueError:
        # 不是 JSON（如网关返回的 502 HTML 页），退回用 HTTP 状态码报错
        raise RuntimeError(f"Prometheus 返回了非 JSON 响应：HTTP {resp.status_code}") from None
    if body.get("status") != "success":
        raise RuntimeError(
            f"Prometheus 查询失败：{body.get('errorType')} - {body.get('error')}"
        )
    data = body.get("data", {})
    if data.get("resultType") != "vector":
        return []
    return data.get("result", [])


async def prom_query_range(
    promql: str, start: float, end: float, step: str = "30s"
) -> list[dict]:
    """打 Prometheus 范围查询 /api/v1/query_range，返回 matrix 结果列表（每项 metric 标签 + values 时序）。

    - start / end 用 unix 秒（float）。step 是采样步长（如 "30s"），控制返回点数别太多。
    - 校验信封同 prom_query；只处理 matrix（区间向量），其它 resultType 返回空列表。
    """
    resp = await get_client().post(
        f"{settings.prometheus_base_url}/api/v1/query_range",
        data={"query": promql, "start": str(start), "end": str(end), "step": step},
    )
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(f"Prometheus 返回了非 JSON 响应：HTTP {resp.status_code}") from None
    if body.get("status") != "success":
        raise RuntimeError(
            f"Prometheus 范围查询失败：{body.get('errorType')} - {body.get('error')}"
        )
    data = body.get("data", {})
    if data.get("resultType") != "matrix":
        return []
    return data.get("result", [])


async def prom_label_values(label: str) -> list[str]:
    """查 Prometheus 某个标签的所有取值 /api/v1/label/<label>/values，返回字符串列表。

    用来做"发现"——比如查 instance 标签的取值，就知道当前有哪些实例在被采集指标。
    """
    resp = await get_client().get(
        f"{settings.prometheus_base_url}/api/v1/label/{label}/values",
    )
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(f"Prometheus 返回了非 JSON 响应：HTTP {resp.status_code}") from None
    if body.get("status") != "success":
        raise RuntimeError(
            f"Prometheus 查询失败：{body.get('errorType')} - {body.get('error')}"
        )
    return body.get("data") or []


async def prom_rule_groups() -> list[dict]:
    """读取 Prometheus 当前已加载的告警规则及运行状态。"""
    resp = await get_client().get(
        f"{settings.prometheus_base_url}/api/v1/rules",
        params={"type": "alert"},
    )
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(
            f"Prometheus 返回了非 JSON 响应：HTTP {resp.status_code}"
        ) from None
    if body.get("status") != "success":
        raise RuntimeError(
            f"Prometheus 查询规则失败：{body.get('errorType')} - {body.get('error')}"
        )
    return (body.get("data") or {}).get("groups") or []


async def prom_reload() -> None:
    """通过官方 lifecycle 端点热重载 Prometheus 配置和规则。"""
    resp = await get_client().post(f"{settings.prometheus_base_url}/-/reload")
    if resp.status_code != 200:
        raise RuntimeError(
            f"Prometheus 热重载失败：HTTP {resp.status_code} - {resp.text[:200]}"
        )


async def loki_query_range(
    logql: str,
    start_ns: int,
    end_ns: int,
    limit: int = 200,
    direction: str = "backward",
) -> list[dict]:
    """打 Loki 的范围查询 /loki/api/v1/query_range，返回 streams 结果列表（每项含 stream 标签 + values）。

    - start/end 用纳秒 unix 时间戳（字符串）。
    - 多租户 Loki 需要 X-Scope-OrgID header；本地单体（loki_org_id 为空）不带。
    - 校验 {"status": "success"} 信封；失败抛 RuntimeError（带上 Loki 的错因，含 LogQL 语法错）。
    - 只处理 streams 结果（日志查询）；metric 类 LogQL（resultType matrix/vector）返回空列表。
    """
    headers = {"X-Scope-OrgID": settings.loki_org_id} if settings.loki_org_id else None
    resp = await get_client().get(
        f"{settings.loki_base_url}/loki/api/v1/query_range",
        params={
            "query": logql,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": str(limit),
            "direction": direction,
        },
        headers=headers,
    )
    try:
        body = resp.json()
    except ValueError:
        # LogQL 语法错时 Loki 常返回非 JSON 的纯文本错误，带上 resp.text 才有诊断价值
        raise RuntimeError(
            f"Loki 返回非 JSON（HTTP {resp.status_code}）：{resp.text[:200]}"
        ) from None
    if body.get("status") != "success":
        raise RuntimeError(f"Loki 查询失败：{body.get('error') or body}")
    data = body.get("data", {})
    if data.get("resultType") != "streams":
        return []
    return data.get("result", [])


async def loki_label_values(label: str) -> list[str]:
    """查 Loki 某个标签的所有取值 /loki/api/v1/label/<label>/values，返回字符串列表。

    用来做"发现"——比如查 service_name 标签的取值，就知道当前有哪些服务往 Loki 送了日志。
    不带时间窗，Loki 按其默认窗口返回近期出现过的取值。
    """
    headers = {"X-Scope-OrgID": settings.loki_org_id} if settings.loki_org_id else None
    resp = await get_client().get(
        f"{settings.loki_base_url}/loki/api/v1/label/{label}/values",
        headers=headers,
    )
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(
            f"Loki 返回非 JSON（HTTP {resp.status_code}）：{resp.text[:200]}"
        ) from None
    if body.get("status") != "success":
        raise RuntimeError(f"Loki 查询失败：{body.get('error') or body}")
    return body.get("data") or []


async def am_get_alerts(
    active: bool = True,
    silenced: bool = False,
    inhibited: bool = False,
) -> list[dict]:
    """打 Alertmanager v2 API GET /api/v2/alerts，返回告警对象列表。

    注意：Alertmanager v2 跟 Prometheus/Loki 不同——**顶层直接是数组**，没有
    {"status": "success", "data": ...} 信封。所以这里靠 HTTP 状态码 + 是不是 list 判断成败。
    默认只取 active（正在触发）、未被 silence/inhibit 抑制的告警。
    """
    resp = await get_client().get(
        f"{settings.alertmanager_base_url}/api/v2/alerts",
        params={
            "active": str(active).lower(),
            "silenced": str(silenced).lower(),
            "inhibited": str(inhibited).lower(),
        },
    )
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(
            f"Alertmanager 返回非 JSON（HTTP {resp.status_code}）：{resp.text[:200]}"
        ) from None
    if resp.status_code != 200:
        raise RuntimeError(f"Alertmanager 查询失败：HTTP {resp.status_code} - {body}")
    if not isinstance(body, list):
        raise RuntimeError(f"Alertmanager 返回了意外结构（期望数组）：{body}")
    return body
