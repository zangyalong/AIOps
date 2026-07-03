from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 固定在 backend/ 目录下。不能写成相对路径 ".env"——它是相对“进程启动时的工作目录”解析的，
# 而不是相对这个文件。我们的 uvicorn 是从仓库根目录用 `uv run --project backend ...` 启动的，
# CWD 是仓库根目录，写相对路径会找不到 backend/.env（之前 DEEPSEEK_API_KEY 一直读不到就是这个原因）。
ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8")

    database_url: str = "postgresql+psycopg://leishaohui@localhost:5432/aioms"
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # DeepSeek 走 OpenAI 兼容接口，base_url/model 以官方文档为准（2026-07 查证）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"


settings = Settings()
