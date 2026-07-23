from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import DBSession
from app.models.service_catalog import Service, ServiceDependency, ServiceInstance
from app.schemas.service_catalog import (
    ServiceCreate,
    ServiceDependencyCreate,
    ServiceDependencyRead,
    ServiceDependencyUpdate,
    ServiceInstanceCreate,
    ServiceInstanceRead,
    ServiceInstanceUpdate,
    ServiceRead,
    ServiceTopologyRead,
    ServiceUpdate,
)
from app.services import service_catalog_service

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


async def _dependency_or_404(
    db: AsyncSession, dependency_id: uuid.UUID
) -> ServiceDependency:
    dependency = await service_catalog_service.get_dependency(db, dependency_id)
    if dependency is None:
        raise HTTPException(status_code=404, detail="service dependency not found")
    return dependency


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
    await service_catalog_service.delete_service(
        db, await _service_or_404(db, service_id)
    )


@router.get("/services/{service_id}/topology", response_model=ServiceTopologyRead)
async def get_service_topology(
    service_id: uuid.UUID,
    db: DBSession,
    max_depth: int = Query(default=3, ge=1, le=8),
) -> service_catalog_service.ServiceTopology:
    try:
        return await service_catalog_service.get_topology(
            db, service_id, max_depth=max_depth
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


@router.patch("/service-instances/{instance_id}", response_model=ServiceInstanceRead)
async def update_service_instance(
    instance_id: uuid.UUID, data: ServiceInstanceUpdate, db: DBSession
) -> ServiceInstance:
    instance = await _instance_or_404(db, instance_id)
    try:
        return await service_catalog_service.update_instance(db, instance, data)
    except ValueError as exc:
        _raise_service_error(exc)


@router.delete("/service-instances/{instance_id}", status_code=204)
async def delete_service_instance(instance_id: uuid.UUID, db: DBSession) -> None:
    await service_catalog_service.delete_instance(
        db, await _instance_or_404(db, instance_id)
    )


@router.get("/service-dependencies", response_model=list[ServiceDependencyRead])
async def list_service_dependencies(
    db: DBSession, service_id: uuid.UUID | None = None
) -> list[ServiceDependency]:
    return await service_catalog_service.list_dependencies(db, service_id)


@router.post(
    "/service-dependencies", response_model=ServiceDependencyRead, status_code=201
)
async def create_service_dependency(
    data: ServiceDependencyCreate, db: DBSession
) -> ServiceDependency:
    try:
        return await service_catalog_service.create_dependency(db, data)
    except ValueError as exc:
        _raise_service_error(exc)


@router.patch(
    "/service-dependencies/{dependency_id}", response_model=ServiceDependencyRead
)
async def update_service_dependency(
    dependency_id: uuid.UUID, data: ServiceDependencyUpdate, db: DBSession
) -> ServiceDependency:
    dependency = await _dependency_or_404(db, dependency_id)
    try:
        return await service_catalog_service.update_dependency(db, dependency, data)
    except ValueError as exc:
        _raise_service_error(exc)


@router.delete("/service-dependencies/{dependency_id}", status_code=204)
async def delete_service_dependency(dependency_id: uuid.UUID, db: DBSession) -> None:
    await service_catalog_service.delete_dependency(
        db, await _dependency_or_404(db, dependency_id)
    )
