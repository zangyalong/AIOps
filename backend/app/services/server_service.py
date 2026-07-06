from __future__ import annotations

import uuid

import asyncssh
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
from app.repositories import server_repository
from app.schemas.server import ConnectionTestResult, ServerCreate, ServerUpdate
from app.services.ssh_service import SSHKeyMissing, SSHNotConfigured, build_connect_params


async def list_servers(db: AsyncSession) -> list[Server]:
    return await server_repository.list_all(db)


async def get_server(db: AsyncSession, server_id: uuid.UUID) -> Server | None:
    return await server_repository.get_by_id(db, server_id)


async def create_server(db: AsyncSession, data: ServerCreate) -> Server:
    server = Server(**data.model_dump())
    return await server_repository.create(db, server)


async def update_server(db: AsyncSession, server: Server, data: ServerUpdate) -> Server:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(server, field, value)
    return await server_repository.save(db, server)


async def delete_server(db: AsyncSession, server: Server) -> None:
    await server_repository.delete(db, server)


async def _probe_connection(server: Server) -> ConnectionTestResult:
    """真的尝试连一下。5 秒连接超时，避免服务器没响应时前端一直转圈。"""
    try:
        params = build_connect_params(server)
    except SSHNotConfigured:
        return ConnectionTestResult(status="unconfigured", detail="没有配置 SSH 连接信息")
    except SSHKeyMissing as exc:
        return ConnectionTestResult(status="unconfigured", detail=str(exc))

    try:
        async with asyncssh.connect(
            params.host,
            port=params.port,
            username=params.username,
            client_keys=params.client_keys,
            known_hosts=None,
            connect_timeout=5,
        ) as conn:
            await conn.run("true", check=True)
        return ConnectionTestResult(status="active", detail="连接成功")
    except (asyncssh.Error, OSError) as exc:
        return ConnectionTestResult(status="unreachable", detail=str(exc))


async def test_connection(db: AsyncSession, server: Server) -> ConnectionTestResult:
    """给前端"测试连接"按钮用：探测一次，并把结果状态写回这一行，刷新页面也能看到上次的状态。"""
    result = await _probe_connection(server)
    server.status = result.status
    await server_repository.save(db, server)
    return result
