from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_catalog import Service, ServiceDependency, ServiceInstance


async def list_services(
    db: AsyncSession, environment: str | None = None
) -> list[Service]:
    stmt = select(Service).order_by(Service.environment, Service.name)
    if environment is not None:
        stmt = stmt.where(Service.environment == environment)
    return list(await db.scalars(stmt))


async def get_service(db: AsyncSession, service_id: uuid.UUID) -> Service | None:
    return await db.get(Service, service_id)


async def list_services_by_name(db: AsyncSession, name: str) -> list[Service]:
    result = await db.scalars(
        select(Service)
        .where(Service.name == name)
        .order_by(Service.environment, Service.created_at)
    )
    return list(result)


async def get_service_by_identity(
    db: AsyncSession, name: str, environment: str
) -> Service | None:
    return await db.scalar(
        select(Service).where(Service.name == name, Service.environment == environment)
    )


async def create_service(db: AsyncSession, service: Service) -> Service:
    db.add(service)
    await db.commit()
    await db.refresh(service)
    return service


async def save_service(db: AsyncSession, service: Service) -> Service:
    await db.commit()
    await db.refresh(service)
    return service


async def delete_service(db: AsyncSession, service: Service) -> None:
    await db.delete(service)
    await db.commit()


async def list_instances(
    db: AsyncSession,
    *,
    service_id: uuid.UUID | None = None,
    server_id: uuid.UUID | None = None,
) -> list[ServiceInstance]:
    stmt = select(ServiceInstance).order_by(ServiceInstance.name)
    if service_id is not None:
        stmt = stmt.where(ServiceInstance.service_id == service_id)
    if server_id is not None:
        stmt = stmt.where(ServiceInstance.server_id == server_id)
    return list(await db.scalars(stmt))


async def list_instances_by_metric_instance(
    db: AsyncSession, metric_instance: str
) -> list[ServiceInstance]:
    result = await db.scalars(
        select(ServiceInstance).where(
            ServiceInstance.metric_instance == metric_instance
        )
    )
    return list(result)


async def get_instance(
    db: AsyncSession, instance_id: uuid.UUID
) -> ServiceInstance | None:
    return await db.get(ServiceInstance, instance_id)


async def get_instance_by_identity(
    db: AsyncSession, service_id: uuid.UUID, name: str
) -> ServiceInstance | None:
    return await db.scalar(
        select(ServiceInstance).where(
            ServiceInstance.service_id == service_id, ServiceInstance.name == name
        )
    )


async def get_instance_by_runtime_identity(
    db: AsyncSession,
    *,
    server_id: uuid.UUID,
    runtime_type: str,
    runtime_name: str,
) -> ServiceInstance | None:
    return await db.scalar(
        select(ServiceInstance).where(
            ServiceInstance.server_id == server_id,
            ServiceInstance.runtime_type == runtime_type,
            ServiceInstance.runtime_name == runtime_name,
        )
    )


async def create_instance(
    db: AsyncSession, instance: ServiceInstance
) -> ServiceInstance:
    db.add(instance)
    await db.commit()
    await db.refresh(instance)
    return instance


async def save_instance(db: AsyncSession, instance: ServiceInstance) -> ServiceInstance:
    await db.commit()
    await db.refresh(instance)
    return instance


async def delete_instance(db: AsyncSession, instance: ServiceInstance) -> None:
    await db.delete(instance)
    await db.commit()


async def list_dependencies(
    db: AsyncSession, service_id: uuid.UUID | None = None
) -> list[ServiceDependency]:
    stmt = select(ServiceDependency).order_by(ServiceDependency.created_at)
    if service_id is not None:
        stmt = stmt.where(
            or_(
                ServiceDependency.upstream_service_id == service_id,
                ServiceDependency.downstream_service_id == service_id,
            )
        )
    return list(await db.scalars(stmt))


async def get_dependency(
    db: AsyncSession, dependency_id: uuid.UUID
) -> ServiceDependency | None:
    return await db.get(ServiceDependency, dependency_id)


async def get_dependency_by_edge(
    db: AsyncSession, upstream_id: uuid.UUID, downstream_id: uuid.UUID
) -> ServiceDependency | None:
    return await db.scalar(
        select(ServiceDependency).where(
            ServiceDependency.upstream_service_id == upstream_id,
            ServiceDependency.downstream_service_id == downstream_id,
        )
    )


async def create_dependency(
    db: AsyncSession, dependency: ServiceDependency
) -> ServiceDependency:
    db.add(dependency)
    await db.commit()
    await db.refresh(dependency)
    return dependency


async def save_dependency(
    db: AsyncSession, dependency: ServiceDependency
) -> ServiceDependency:
    await db.commit()
    await db.refresh(dependency)
    return dependency


async def delete_dependency(db: AsyncSession, dependency: ServiceDependency) -> None:
    await db.delete(dependency)
    await db.commit()
