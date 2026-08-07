from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_catalog import Service, ServiceFileAccess, ServiceInstance


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


async def list_file_accesses(
    db: AsyncSession,
    *,
    service_instance_id: uuid.UUID | None = None,
) -> list[ServiceFileAccess]:
    stmt = select(ServiceFileAccess).order_by(
        ServiceFileAccess.service_instance_id,
        ServiceFileAccess.access_level,
        ServiceFileAccess.path,
    )
    if service_instance_id is not None:
        stmt = stmt.where(
            ServiceFileAccess.service_instance_id == service_instance_id
        )
    return list(await db.scalars(stmt))


async def get_file_access(
    db: AsyncSession, access_id: uuid.UUID
) -> ServiceFileAccess | None:
    return await db.get(ServiceFileAccess, access_id)


async def get_file_access_by_identity(
    db: AsyncSession,
    *,
    service_instance_id: uuid.UUID,
    access_level: str,
    path: str,
) -> ServiceFileAccess | None:
    return await db.scalar(
        select(ServiceFileAccess).where(
            ServiceFileAccess.service_instance_id == service_instance_id,
            ServiceFileAccess.access_level == access_level,
            ServiceFileAccess.path == path,
        )
    )


async def create_file_access(
    db: AsyncSession, access: ServiceFileAccess
) -> ServiceFileAccess:
    db.add(access)
    await db.commit()
    await db.refresh(access)
    return access


async def delete_file_access(db: AsyncSession, access: ServiceFileAccess) -> None:
    await db.delete(access)
    await db.commit()
