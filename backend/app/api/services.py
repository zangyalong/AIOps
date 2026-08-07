from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import DBSession
from app.models.server import Server
from app.models.service_catalog import Service, ServiceFileAccess, ServiceInstance
from app.schemas.service_catalog import (
    ServiceControlAuthorizationUpdate,
    ServiceFileAccessCreate,
    ServiceFileAccessRead,
    ServiceCreate,
    ServiceInstanceCreate,
    ServiceInstanceRead,
    ServiceInstanceUpdate,
    ServiceRead,
    ServiceUpdate,
)
from app.schemas.service_observability import (
    ServiceInstanceStatusRead,
    ServiceObservabilityRead,
)
from app.services import (
    server_service,
    service_catalog_service,
    service_control_policy_service,
    service_observability_service,
    service_runtime_service,
    server_file_policy_service,
)

router = APIRouter(tags=["service-catalog"])


def _raise_service_error(exc: ValueError) -> None:
    if isinstance(exc, service_catalog_service.DirectoryNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, service_catalog_service.DirectoryConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _service_or_404(db: AsyncSession, service_id: uuid.UUID) -> Service:
    service = await service_catalog_service.get_service(db, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="service not found")
    return service


async def _instance_or_404(db: AsyncSession, instance_id: uuid.UUID) -> ServiceInstance:
    instance = await service_catalog_service.get_instance(db, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="service instance not found")
    return instance


async def _file_access_or_404(
    db: AsyncSession, access_id: uuid.UUID
) -> ServiceFileAccess:
    access = await service_catalog_service.get_file_access(db, access_id)
    if access is None:
        raise HTTPException(status_code=404, detail="service file access not found")
    return access


@router.get("/services", response_model=list[ServiceRead])
async def list_services(db: DBSession, environment: str | None = None) -> list[Service]:
    return await service_catalog_service.list_services(db, environment)


@router.post("/services", response_model=ServiceRead, status_code=201)
async def create_service(data: ServiceCreate, db: DBSession) -> Service:
    try:
        return await service_catalog_service.create_service(db, data)
    except ValueError as exc:
        _raise_service_error(exc)


@router.get("/services/{service_id}", response_model=ServiceRead)
async def get_service(service_id: uuid.UUID, db: DBSession) -> Service:
    return await _service_or_404(db, service_id)


@router.patch("/services/{service_id}", response_model=ServiceRead)
async def update_service(
    service_id: uuid.UUID, data: ServiceUpdate, db: DBSession
) -> Service:
    service = await _service_or_404(db, service_id)
    try:
        return await service_catalog_service.update_service(db, service, data)
    except ValueError as exc:
        _raise_service_error(exc)


@router.delete("/services/{service_id}", status_code=204)
async def delete_service(service_id: uuid.UUID, db: DBSession) -> None:
    try:
        await service_catalog_service.delete_service(
            db, await _service_or_404(db, service_id)
        )
    except ValueError as exc:
        _raise_service_error(exc)


@router.get("/service-instances", response_model=list[ServiceInstanceRead])
async def list_service_instances(
    db: DBSession,
    service_id: uuid.UUID | None = None,
    server_id: uuid.UUID | None = None,
) -> list[ServiceInstance]:
    return await service_catalog_service.list_instances(
        db, service_id=service_id, server_id=server_id
    )


@router.post("/service-instances", response_model=ServiceInstanceRead, status_code=201)
async def create_service_instance(
    data: ServiceInstanceCreate, db: DBSession
) -> ServiceInstance:
    try:
        return await service_catalog_service.create_instance(db, data)
    except ValueError as exc:
        _raise_service_error(exc)


@router.get(
    "/service-instances/statuses", response_model=list[ServiceInstanceStatusRead]
)
async def list_service_instance_statuses(
    db: DBSession,
    service_id: uuid.UUID | None = None,
    server_id: uuid.UUID | None = None,
) -> list[ServiceInstanceStatusRead]:
    instances = await service_catalog_service.list_instances(
        db, service_id=service_id, server_id=server_id
    )
    server_ids = {item.server_id for item in instances if item.server_id is not None}
    servers: dict[uuid.UUID, Server] = {}
    for current_server_id in server_ids:
        server = await server_service.get_server(db, current_server_id)
        if server is not None:
            servers[current_server_id] = server
    return await service_runtime_service.check_instance_statuses(instances, servers)


@router.get(
    "/service-instances/{instance_id}/observability",
    response_model=ServiceObservabilityRead,
)
async def get_service_instance_observability(
    instance_id: uuid.UUID,
    db: DBSession,
    minutes: int = Query(default=1440, ge=5, le=10080),
) -> ServiceObservabilityRead:
    instance = await _instance_or_404(db, instance_id)
    service = await _service_or_404(db, instance.service_id)
    server = (
        await server_service.get_server(db, instance.server_id)
        if instance.server_id is not None
        else None
    )
    return await service_observability_service.get_instance_observability(
        service=service,
        instance=instance,
        server=server,
        minutes=minutes,
    )


@router.patch("/service-instances/{instance_id}", response_model=ServiceInstanceRead)
async def update_service_instance(
    instance_id: uuid.UUID, data: ServiceInstanceUpdate, db: DBSession
) -> ServiceInstance:
    instance = await _instance_or_404(db, instance_id)
    try:
        return await service_catalog_service.update_instance(db, instance, data)
    except ValueError as exc:
        _raise_service_error(exc)


@router.patch(
    "/service-instances/{instance_id}/control-authorization",
    response_model=ServiceInstanceRead,
)
async def update_service_control_authorization(
    instance_id: uuid.UUID,
    data: ServiceControlAuthorizationUpdate,
    db: DBSession,
) -> ServiceInstance:
    instance = await _instance_or_404(db, instance_id)
    try:
        return await service_control_policy_service.update_control_authorization(
            db, instance, enabled=data.enabled
        )
    except service_control_policy_service.ServiceControlPolicySyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        _raise_service_error(exc)


@router.get("/service-file-accesses", response_model=list[ServiceFileAccessRead])
async def list_service_file_accesses(
    db: DBSession,
    service_instance_id: uuid.UUID | None = None,
) -> list[ServiceFileAccess]:
    return await service_catalog_service.list_file_accesses(
        db, service_instance_id=service_instance_id
    )


@router.post(
    "/service-instances/{instance_id}/file-accesses",
    response_model=ServiceFileAccessRead,
    status_code=201,
)
async def create_service_file_access(
    instance_id: uuid.UUID,
    data: ServiceFileAccessCreate,
    db: DBSession,
) -> ServiceFileAccess:
    instance = await _instance_or_404(db, instance_id)
    try:
        return await server_file_policy_service.create_file_access(
            db,
            instance,
            access_level=data.access_level,
            path=data.path,
        )
    except server_file_policy_service.ServerFilePolicySyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        _raise_service_error(exc)


@router.delete("/service-file-accesses/{access_id}", status_code=204)
async def delete_service_file_access(
    access_id: uuid.UUID,
    db: DBSession,
) -> None:
    try:
        await server_file_policy_service.delete_file_access(
            db, await _file_access_or_404(db, access_id)
        )
    except server_file_policy_service.ServerFilePolicySyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        _raise_service_error(exc)


@router.delete("/service-instances/{instance_id}", status_code=204)
async def delete_service_instance(instance_id: uuid.UUID, db: DBSession) -> None:
    try:
        await service_catalog_service.delete_instance(
            db, await _instance_or_404(db, instance_id)
        )
    except ValueError as exc:
        _raise_service_error(exc)
