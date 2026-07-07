"""可观测性数据源（Prometheus/Loki/Alertmanager）的公共 HTTP 访问层。

这些数据源的调用本质都是"发一个 HTTP 请求 + 解析固定结构的 JSON"，没必要为每个上一整套
客户端库 / provider 框架。这里只抽真正共用的东西：一个进程级复用的 httpx.AsyncClient
（复用连接池），以及各数据源信封校验的小函数。每个工具各自拼自己的查询、格式化自己的结果，
不在这里耦合。

现在只有 Prometheus（任务1）；Loki（任务2）、Alertmanager（任务3）用到时再往这里加对应的
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
