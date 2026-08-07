from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.dependencies import DBSession
from app.models.server import Server
from app.schemas.server import (
    ConnectionTestResult,
    DeploymentBundleRequest,
    ServerCreate,
    ServerRead,
    ServerUpdate,
)
from app.schemas.service_observability import ServerObservabilityRead
from app.services import server_service, service_observability_service
from app.services.target_deployment_service import (
    DeploymentBundleError,
    build_deployment_bundle,
    cleanup_bundle,
)

router = APIRouter(prefix="/servers", tags=["servers"])


async def _get_server_or_404(db: AsyncSession, server_id: uuid.UUID) -> Server:
    server = await server_service.get_server(db, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")
    return server


@router.get("", response_model=list[ServerRead])
async def list_servers(db: DBSession) -> list[Server]:
    return await server_service.list_servers(db)


@router.post("", response_model=ServerRead, status_code=201)
async def create_server(data: ServerCreate, db: DBSession) -> Server:
    return await server_service.create_server(db, data)


@router.get("/{server_id}", response_model=ServerRead)
async def get_server(server_id: uuid.UUID, db: DBSession) -> Server:
    return await _get_server_or_404(db, server_id)


@router.get(
    "/{server_id}/observability",
    response_model=ServerObservabilityRead,
)
async def get_server_observability(
    server_id: uuid.UUID,
    db: DBSession,
    minutes: int = Query(default=60, ge=15, le=10080),
) -> ServerObservabilityRead:
    server = await _get_server_or_404(db, server_id)
    return await service_observability_service.get_server_observability(server, minutes)


@router.patch("/{server_id}", response_model=ServerRead)
async def update_server(
    server_id: uuid.UUID, data: ServerUpdate, db: DBSession
) -> Server:
    server = await _get_server_or_404(db, server_id)
    return await server_service.update_server(db, server, data)


@router.delete("/{server_id}", status_code=204)
async def delete_server(server_id: uuid.UUID, db: DBSession) -> None:
    server = await _get_server_or_404(db, server_id)
    await server_service.delete_server(db, server)


@router.post("/{server_id}/test-connection", response_model=ConnectionTestResult)
async def test_server_connection(
    server_id: uuid.UUID, db: DBSession
) -> ConnectionTestResult:
    server = await _get_server_or_404(db, server_id)
    return await server_service.test_connection(db, server)


@router.post(
    "/{server_id}/deployment-bundle",
    response_class=FileResponse,
    responses={200: {"content": {"application/gzip": {}}}},
)
async def download_deployment_bundle(
    server_id: uuid.UUID,
    data: DeploymentBundleRequest,
    db: DBSession,
) -> FileResponse:
    """生成不含私钥的首次安装包；数据库凭据只存在于响应期临时目录。"""
    server = await _get_server_or_404(db, server_id)
    try:
        artifact = await build_deployment_bundle(server, data.secrets)
    except DeploymentBundleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        await server_service.record_generated_ssh_keys(
            db,
            server,
            key_filename=artifact.key_filename,
        )
    except Exception:
        cleanup_bundle(artifact.temporary_root)
        raise

    return FileResponse(
        path=artifact.archive_path,
        media_type="application/gzip",
        filename=artifact.archive_path.name,
        headers={"X-Checksum-SHA256": artifact.sha256},
        background=BackgroundTask(cleanup_bundle, artifact.temporary_root),
    )
