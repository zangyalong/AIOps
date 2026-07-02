from __future__ import annotations

from fastapi import FastAPI

from app.api import health, servers

app = FastAPI(title="AIOMS Backend")

app.include_router(health.router)
app.include_router(servers.router)
