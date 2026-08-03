from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
from app.models.service_catalog import Service, ServiceDependency, ServiceInstance
from app.repositories import service_catalog_repository
from app.schemas.service_catalog import (
    ServiceCreate,
    ServiceDependencyCreate,
    ServiceDependencyUpdate,
    ServiceInstanceCreate,
    ServiceInstanceUpdate,
    ServiceUpdate,
)
from app.services import server_service


class DirectoryNotFound(ValueError):
    pass


class DirectoryConflict(ValueError):
    pass


class InvalidDependency(ValueError):
    pass


class AmbiguousService(ValueError):
    pass


@dataclass(frozen=True)
class ServiceTopology:
    focus_service_id: uuid.UUID
    nodes: list[Service]
    edges: list[ServiceDependency]


@dataclass(frozen=True)
class ResolvedServiceInstance:
    service: Service
    instance: ServiceInstance


@dataclass(frozen=True)
class DiscoveredServiceRegistration:
    service_name: str
    instance_name: str
    runtime_type: str
    runtime_name: str
    description: str | None = None


@dataclass(frozen=True)
class ServiceRegistrationResult:
    created_services: int
    created_instances: int
    skipped_instances: int


async def list_services(
    db: AsyncSession, environment: str | None = None
) -> list[Service]:
    return await service_catalog_repository.list_services(db, environment)


async def get_service(db: AsyncSession, service_id: uuid.UUID) -> Service | None:
    return await service_catalog_repository.get_service(db, service_id)


async def resolve_service(
    db: AsyncSession, name: str, environment: str | None = None
) -> Service | None:
    if environment:
        return await service_catalog_repository.get_service_by_identity(
            db, name, environment
        )
    matches = await service_catalog_repository.list_services_by_name(db, name)
    if len(matches) > 1:
        environments = "、".join(item.environment for item in matches)
        raise AmbiguousService(
            f"服务 {name} 存在多个环境：{environments}，请指定 environment"
        )
    return matches[0] if matches else None


async def create_service(db: AsyncSession, data: ServiceCreate) -> Service:
    existing = await service_catalog_repository.get_service_by_identity(
        db, data.name, data.environment
    )
    if existing is not None:
        raise DirectoryConflict(f"服务 {data.name}/{data.environment} 已存在")
    return await service_catalog_repository.create_service(
        db, Service(**data.model_dump())
    )


async def update_service(
    db: AsyncSession, service: Service, data: ServiceUpdate
) -> Service:
    values = data.model_dump(exclude_unset=True)
    name = values.get("name", service.name)
    environment = values.get("environment", service.environment)
    existing = await service_catalog_repository.get_service_by_identity(
        db, name, environment
    )
    if existing is not None and existing.id != service.id:
        raise DirectoryConflict(f"服务 {name}/{environment} 已存在")
    for field, value in values.items():
        setattr(service, field, value)
    return await service_catalog_repository.save_service(db, service)


async def delete_service(db: AsyncSession, service: Service) -> None:
    await service_catalog_repository.delete_service(db, service)


async def list_instances(
    db: AsyncSession,
    *,
    service_id: uuid.UUID | None = None,
    server_id: uuid.UUID | None = None,
) -> list[ServiceInstance]:
    return await service_catalog_repository.list_instances(
        db, service_id=service_id, server_id=server_id
    )


async def get_instance(
    db: AsyncSession, instance_id: uuid.UUID
) -> ServiceInstance | None:
    return await service_catalog_repository.get_instance(db, instance_id)


async def _validate_instance_references(
    db: AsyncSession, service_id: uuid.UUID, server_id: uuid.UUID | None
) -> None:
    if await get_service(db, service_id) is None:
        raise DirectoryNotFound("service not found")
    if server_id is not None and await server_service.get_server(db, server_id) is None:
        raise DirectoryNotFound("server not found")


def _validate_runtime_identity(
    *,
    server_id: uuid.UUID | None,
    runtime_type: str | None,
    runtime_name: str | None,
) -> None:
    if bool(runtime_type) != bool(runtime_name):
        raise ValueError("runtime_type 和 runtime_name 必须同时填写")
    if runtime_type and server_id is None:
        raise ValueError("填写运行时身份时必须绑定服务器")


async def _ensure_runtime_identity_available(
    db: AsyncSession,
    *,
    server_id: uuid.UUID | None,
    runtime_type: str | None,
    runtime_name: str | None,
    current_instance_id: uuid.UUID | None = None,
) -> None:
    _validate_runtime_identity(
        server_id=server_id,
        runtime_type=runtime_type,
        runtime_name=runtime_name,
    )
    if server_id is None or runtime_type is None or runtime_name is None:
        return
    existing = await service_catalog_repository.get_instance_by_runtime_identity(
        db,
        server_id=server_id,
        runtime_type=runtime_type,
        runtime_name=runtime_name,
    )
    if existing is not None and existing.id != current_instance_id:
        raise DirectoryConflict("该服务器上的运行时对象已经登记到服务目录")


async def create_instance(
    db: AsyncSession, data: ServiceInstanceCreate
) -> ServiceInstance:
    await _validate_instance_references(db, data.service_id, data.server_id)
    await _ensure_runtime_identity_available(
        db,
        server_id=data.server_id,
        runtime_type=data.runtime_type,
        runtime_name=data.runtime_name,
    )
    existing = await service_catalog_repository.get_instance_by_identity(
        db, data.service_id, data.name
    )
    if existing is not None:
        raise DirectoryConflict("该服务下已存在同名实例")
    return await service_catalog_repository.create_instance(
        db, ServiceInstance(**data.model_dump())
    )


async def register_discovered_services(
    db: AsyncSession,
    *,
    server: Server,
    environment: str,
    registrations: list[DiscoveredServiceRegistration],
) -> ServiceRegistrationResult:
    """幂等地注册扫描结果；已有逻辑服务复用，已有标准实例名跳过。"""
    created_services = 0
    created_instances = 0
    skipped_instances = 0

    for registration in registrations:
        existing_runtime = (
            await service_catalog_repository.get_instance_by_runtime_identity(
                db,
                server_id=server.id,
                runtime_type=registration.runtime_type,
                runtime_name=registration.runtime_name,
            )
        )
        if existing_runtime is not None:
            skipped_instances += 1
            continue

        service = await service_catalog_repository.get_service_by_identity(
            db, registration.service_name, environment
        )
        if service is None:
            service = await create_service(
                db,
                ServiceCreate(
                    name=registration.service_name,
                    environment=environment,
                ),
            )
            created_services += 1

        existing = await service_catalog_repository.get_instance_by_identity(
            db, service.id, registration.instance_name
        )
        if existing is not None:
            if existing.runtime_type is None and existing.runtime_name is None:
                existing.runtime_type = registration.runtime_type
                existing.runtime_name = registration.runtime_name
                existing.log_service = existing.log_service or registration.service_name
                await service_catalog_repository.save_instance(db, existing)
            skipped_instances += 1
            continue

        await create_instance(
            db,
            ServiceInstanceCreate(
                service_id=service.id,
                server_id=server.id,
                name=registration.instance_name,
                runtime_type=registration.runtime_type,
                runtime_name=registration.runtime_name,
                log_service=registration.service_name,
                description=registration.description,
            ),
        )
        created_instances += 1

    return ServiceRegistrationResult(
        created_services=created_services,
        created_instances=created_instances,
        skipped_instances=skipped_instances,
    )


async def update_instance(
    db: AsyncSession, instance: ServiceInstance, data: ServiceInstanceUpdate
) -> ServiceInstance:
    values = data.model_dump(exclude_unset=True)
    service_id = values.get("service_id", instance.service_id)
    server_id = values.get("server_id", instance.server_id)
    name = values.get("name", instance.name)
    runtime_type = values.get("runtime_type", instance.runtime_type)
    runtime_name = values.get("runtime_name", instance.runtime_name)
    await _validate_instance_references(db, service_id, server_id)
    await _ensure_runtime_identity_available(
        db,
        server_id=server_id,
        runtime_type=runtime_type,
        runtime_name=runtime_name,
        current_instance_id=instance.id,
    )
    existing = await service_catalog_repository.get_instance_by_identity(
        db, service_id, name
    )
    if existing is not None and existing.id != instance.id:
        raise DirectoryConflict("该服务下已存在同名实例")
    for field, value in values.items():
        setattr(instance, field, value)
    return await service_catalog_repository.save_instance(db, instance)


async def delete_instance(db: AsyncSession, instance: ServiceInstance) -> None:
    await service_catalog_repository.delete_instance(db, instance)


async def list_dependencies(
    db: AsyncSession, service_id: uuid.UUID | None = None
) -> list[ServiceDependency]:
    return await service_catalog_repository.list_dependencies(db, service_id)


async def get_dependency(
    db: AsyncSession, dependency_id: uuid.UUID
) -> ServiceDependency | None:
    return await service_catalog_repository.get_dependency(db, dependency_id)


async def _validate_dependency(
    db: AsyncSession, upstream_id: uuid.UUID, downstream_id: uuid.UUID
) -> None:
    if upstream_id == downstream_id:
        raise InvalidDependency("服务不能依赖自身")
    if await get_service(db, upstream_id) is None:
        raise DirectoryNotFound("upstream service not found")
    if await get_service(db, downstream_id) is None:
        raise DirectoryNotFound("downstream service not found")


async def create_dependency(
    db: AsyncSession, data: ServiceDependencyCreate
) -> ServiceDependency:
    await _validate_dependency(db, data.upstream_service_id, data.downstream_service_id)
    existing = await service_catalog_repository.get_dependency_by_edge(
        db, data.upstream_service_id, data.downstream_service_id
    )
    if existing is not None:
        raise DirectoryConflict("该依赖关系已经存在")
    return await service_catalog_repository.create_dependency(
        db, ServiceDependency(**data.model_dump())
    )


async def update_dependency(
    db: AsyncSession,
    dependency: ServiceDependency,
    data: ServiceDependencyUpdate,
) -> ServiceDependency:
    values = data.model_dump(exclude_unset=True)
    upstream_id = values.get("upstream_service_id", dependency.upstream_service_id)
    downstream_id = values.get(
        "downstream_service_id", dependency.downstream_service_id
    )
    await _validate_dependency(db, upstream_id, downstream_id)
    existing = await service_catalog_repository.get_dependency_by_edge(
        db, upstream_id, downstream_id
    )
    if existing is not None and existing.id != dependency.id:
        raise DirectoryConflict("该依赖关系已经存在")
    for field, value in values.items():
        setattr(dependency, field, value)
    return await service_catalog_repository.save_dependency(db, dependency)


async def delete_dependency(db: AsyncSession, dependency: ServiceDependency) -> None:
    await service_catalog_repository.delete_dependency(db, dependency)


async def get_topology(
    db: AsyncSession, focus_service_id: uuid.UUID, *, max_depth: int = 3
) -> ServiceTopology:
    if await get_service(db, focus_service_id) is None:
        raise DirectoryNotFound("service not found")
    services = await list_services(db)
    dependencies = await list_dependencies(db)
    visited = {focus_service_id}
    frontier = {focus_service_id}
    for _ in range(max_depth):
        next_frontier: set[uuid.UUID] = set()
        for edge in dependencies:
            if edge.upstream_service_id in frontier:
                next_frontier.add(edge.downstream_service_id)
            if edge.downstream_service_id in frontier:
                next_frontier.add(edge.upstream_service_id)
        next_frontier -= visited
        if not next_frontier:
            break
        visited.update(next_frontier)
        frontier = next_frontier
    nodes = [service for service in services if service.id in visited]
    edges = [
        edge
        for edge in dependencies
        if edge.upstream_service_id in visited and edge.downstream_service_id in visited
    ]
    return ServiceTopology(focus_service_id, nodes, edges)


async def resolve_by_metric_instance(
    db: AsyncSession, metric_instance: str | None
) -> ResolvedServiceInstance | None:
    if not metric_instance:
        return None
    instances = await service_catalog_repository.list_instances_by_metric_instance(
        db, metric_instance
    )
    if len(instances) != 1:
        return None
    service = await get_service(db, instances[0].service_id)
    if service is None:
        return None
    return ResolvedServiceInstance(service=service, instance=instances[0])
