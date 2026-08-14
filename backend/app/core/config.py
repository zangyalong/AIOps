from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 固定在 backend/ 目录下。不能写成相对路径 ".env"——它是相对“进程启动时的工作目录”解析的，
# 而不是相对这个文件。我们的 uvicorn 是从仓库根目录用 `uv run --project backend ...` 启动的，
# CWD 是仓库根目录，写相对路径会找不到 backend/.env（之前 DEEPSEEK_API_KEY 一直读不到就是这个原因）。
ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
# 仓库根目录：从这个文件（backend/app/core/config.py）往上数 4 层。
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8")

    database_url: str = "postgresql+psycopg://leishaohui@localhost:5432/aiops"
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # SSH 私钥统一放仓库根目录的 ssh/ 下（.gitignore 忽略，不提交仓库），
    # servers 表里只存只读/部署私钥文件名，实际路径都是 ssh_key_dir / 文件名拼出来的。
    ssh_key_dir: Path = REPO_ROOT / "ssh"
    # 前端“生成环境部署包”调用这里的固定脚本；可在测试环境覆盖为临时目录。
    target_server_scripts_dir: Path = REPO_ROOT / "scripts" / "target-server"
    # AIOps 自身的运维 Skill 目录；每轮只拼装索引，正文由 read_skill 按需读取。
    skills_dir: Path = REPO_ROOT / "backend" / "skills"
    # UI 管理的 Prometheus 告警规则输出文件；Docker 挂载整个目录，以便原子替换后仍可见。
    prometheus_rules_path: Path = (
        REPO_ROOT / "docker" / "prometheus-rules" / "managed_alert_rules.yml"
    )
    # 有副作用的 systemd / Docker 启停重启工具共用独立白名单；空字符串=禁止全部控制。
    ssh_service_control_allowlist: str = ""
    # 兼容旧环境变量；新配置优先使用 SSH_SERVICE_CONTROL_ALLOWLIST。
    ssh_restart_service_allowlist: str = ""

    @property
    def service_control_allowlist(self) -> str:
        return self.ssh_service_control_allowlist or self.ssh_restart_service_allowlist

    # DeepSeek 走 OpenAI 兼容接口，base_url/model 以官方文档为准（2026-07 查证）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"

    # ---- Agent 长期记忆 ----
    # 当前复用 DeepSeek 做事实抽取，检索先使用 PostgreSQL 记录上的确定性关键词评分；以后配置
    # 独立 embedding 模型时可原位切换 pgvector，不影响 memories 表和上层调用。
    memory_enabled: bool = True
    memory_retrieval_limit: int = 6
    memory_candidate_limit: int = 200
    # 全量聊天记录仍留在 messages 表；只限制每轮送给模型的近期历史，避免上下文无限增长。
    memory_short_term_max_chars: int = 120_000

    # ---- 可观测性数据源（PLG），本地默认地址，无认证 ----
    # 地址非敏感，进不进 .env 都行；真接了带 token 的远端再加 *_token 字段。
    prometheus_base_url: str = "http://localhost:9090"
    loki_base_url: str = "http://localhost:3100"
    # 多租户 Loki 才需要传 X-Scope-OrgID；本地单体留空即可
    loki_org_id: str = ""
    alertmanager_base_url: str = "http://localhost:9093"
    # Grafana（可视化）。用于拼 Explore 链接和服务器 Dashboard 深入查看链接，不发请求。
    grafana_base_url: str = "http://localhost:3000"
    # 打可观测性数据源 HTTP 请求的统一超时（秒）
    obs_http_timeout: float = 10.0

    # ---- 方向二：后台任务队列（arq + Redis）----
    redis_url: str = "redis://localhost:6379"

    # ---- Operation Broker 策略身份（当前为本地单用户模式）----
    # 项目尚未接入登录鉴权，因此身份只从服务端配置读取，不能由前端请求自报角色。
    operation_actor_id: str = "local-user"
    operation_actor_role: Literal["viewer", "operator", "approver", "admin"] = "admin"
    # 默认关闭；开启后也只自动放行明确标为非生产环境的 medium 写操作。
    operation_auto_approve_non_production_medium: bool = False


settings = Settings()
