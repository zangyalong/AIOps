from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 固定在 backend/ 目录下。不能写成相对路径 ".env"——它是相对“进程启动时的工作目录”解析的，
# 而不是相对这个文件。我们的 uvicorn 是从仓库根目录用 `uv run --project backend ...` 启动的，
# CWD 是仓库根目录，写相对路径会找不到 backend/.env（之前 DEEPSEEK_API_KEY 一直读不到就是这个原因）。
ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
# 仓库根目录：从这个文件（backend/app/core/config.py）往上数 4 层。
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8")

    database_url: str = "postgresql+psycopg://leishaohui@localhost:5432/aioms"
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # SSH 私钥统一放仓库根目录的 ssh/ 下（.gitignore 忽略，不提交仓库），
    # servers 表里只存只读/部署私钥文件名，实际路径都是 ssh_key_dir / 文件名拼出来的。
    ssh_key_dir: Path = REPO_ROOT / "ssh"
    # 有副作用的服务重启写工具使用独立白名单；默认空字符串=禁止重启任何服务。
    ssh_restart_service_allowlist: str = ""

    # DeepSeek 走 OpenAI 兼容接口，base_url/model 以官方文档为准（2026-07 查证）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"

    # ---- 可观测性数据源（PLG），本地默认地址，无认证 ----
    # 地址非敏感，进不进 .env 都行；真接了带 token 的远端再加 *_token 字段。
    prometheus_base_url: str = "http://localhost:9090"
    loki_base_url: str = "http://localhost:3100"
    # 多租户 Loki 才需要传 X-Scope-OrgID；本地单体留空即可
    loki_org_id: str = ""
    alertmanager_base_url: str = "http://localhost:9093"
    # Grafana（可视化）。只用来给用户拼 Explore 图表链接，不发请求。
    grafana_base_url: str = "http://localhost:3000"
    # 打可观测性数据源 HTTP 请求的统一超时（秒）
    obs_http_timeout: float = 10.0

    # ---- 方向二：后台任务队列（arq + Redis）----
    redis_url: str = "redis://localhost:6379"


settings = Settings()
