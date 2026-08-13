from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.server import Server
from app.models.service_catalog import Service, ServiceFileAccess, ServiceInstance
from app.repositories import service_catalog_repository
from app.schemas.service_catalog import (
    ServiceCreate,
    ServiceInstanceCreate,
    ServiceInstanceUpdate,
    ServiceUpdate,
)
from app.services import server_service


class DirectoryNotFound(ValueError):
    pass


class DirectoryConflict(ValueError):
    pass


class AmbiguousService(ValueError):
    pass


class ControlAuthorizationUnavailable(ValueError):
    pass


class FileAccessAuthorizationUnavailable(ValueError):
    pass


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


SERVICE_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service$")
DOCKER_CONTAINER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
CONTROLLABLE_RUNTIME_TYPES = frozenset({"systemd", "docker"})


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
    instances = await list_instances(db, service_id=service.id)
    if any(instance.control_enabled for instance in instances):
        raise ControlAuthorizationUnavailable(
            "该服务仍有已开启控制授权的实例，请先逐一关闭授权再删除服务"
        )
    for instance in instances:
        if await list_file_accesses(db, service_instance_id=instance.id):
            raise FileAccessAuthorizationUnavailable(
                "该服务仍有服务器文件授权，请先逐一移除授权再删除服务"
            )
    await service_catalog_repository.delete_service(db, service)


async def list_file_accesses(
    db: AsyncSession,
    *,
    service_instance_id: uuid.UUID | None = None,
) -> list[ServiceFileAccess]:
    return await service_catalog_repository.list_file_accesses(
        db, service_instance_id=service_instance_id
    )


async def get_file_access(
    db: AsyncSession, access_id: uuid.UUID
) -> ServiceFileAccess | None:
    return await service_catalog_repository.get_file_access(db, access_id)


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


async def get_instance_by_runtime_identity(
    db: AsyncSession,
    *,
    server_id: uuid.UUID,
    runtime_type: str,
    runtime_name: str,
) -> ServiceInstance | None:
    return await service_catalog_repository.get_instance_by_runtime_identity(
        db,
        server_id=server_id,
        runtime_type=runtime_type,
        runtime_name=runtime_name,
    )


async def resolve_control_target(
    db: AsyncSession,
    *,
    server_id: uuid.UUID,
    service_name: str,
    environment: str = "",
    instance_name: str = "",
) -> ResolvedServiceInstance:
    """把业务服务选择条件解析成唯一且已授权的真实运行对象。"""
    service = await resolve_service(db, service_name, environment or None)
    if service is None:
        suffix = f"/{environment}" if environment else ""
        raise DirectoryNotFound(f"服务目录中没有找到 {service_name}{suffix}")

    candidates = [
        instance
        for instance in await list_instances(
            db,
            service_id=service.id,
            server_id=server_id,
        )
        if not instance_name or instance.name == instance_name
    ]
    if not candidates:
        detail = f"的实例 {instance_name}" if instance_name else "的部署实例"
        raise DirectoryNotFound(
            f"该服务器上没有登记 {service.name}/{service.environment}{detail}"
        )
    if len(candidates) > 1:
        names = "、".join(instance.name for instance in candidates)
        raise AmbiguousService(
            f"该服务器上的 {service.name}/{service.environment} 存在多个实例：{names}，"
            "请指定 instance_name"
        )

    instance = candidates[0]
    validate_control_target(instance)
    if not instance.control_enabled:
        raise ControlAuthorizationUnavailable(
            f"实例 {instance.name} 未开启控制授权，请先在服务目录开启"
        )
    return ResolvedServiceInstance(service=service, instance=instance)


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
    runtime_identity_changed = (
        server_id != instance.server_id
        or runtime_type != instance.runtime_type
        or runtime_name != instance.runtime_name
    )
    if instance.control_enabled and runtime_identity_changed:
        raise ControlAuthorizationUnavailable(
            "该实例已开启控制授权，请先关闭授权再修改服务器或运行时身份"
        )
    if runtime_identity_changed and await list_file_accesses(
        db, service_instance_id=instance.id
    ):
        raise FileAccessAuthorizationUnavailable(
            "该实例仍有服务器文件授权，请先移除授权再修改服务器或运行时身份"
        )
    for field, value in values.items():
        setattr(instance, field, value)
    return await service_catalog_repository.save_instance(db, instance)


async def set_control_authorization(
    db: AsyncSession,
    instance: ServiceInstance,
    *,
    enabled: bool,
) -> ServiceInstance:
    """修改实例级服务控制授权；目标机 root 白名单仍是独立权限上限。"""
    if enabled:
        validate_control_target(instance)
    instance.control_enabled = enabled
    return await service_catalog_repository.save_instance(db, instance)


def validate_control_target(instance: ServiceInstance) -> None:
    if instance.server_id is None:
        raise ControlAuthorizationUnavailable("控制授权要求实例绑定服务器")
    if instance.runtime_type not in CONTROLLABLE_RUNTIME_TYPES:
        raise ControlAuthorizationUnavailable(
            "只有 systemd 服务和 Docker 容器支持启动、停止和重启授权"
        )
    runtime_name = instance.runtime_name or ""
    if instance.runtime_type == "systemd" and not SERVICE_UNIT_PATTERN.fullmatch(
        runtime_name
    ):
        raise ControlAuthorizationUnavailable(
            "控制授权要求填写完整、合法的 systemd .service 单元名"
        )
    if instance.runtime_type == "docker" and not DOCKER_CONTAINER_PATTERN.fullmatch(
        runtime_name
    ):
        raise ControlAuthorizationUnavailable(
            "控制授权要求填写完整、合法的 Docker 容器名"
        )


async def delete_instance(db: AsyncSession, instance: ServiceInstance) -> None:
    if instance.control_enabled:
        raise ControlAuthorizationUnavailable(
            "该实例已开启控制授权，请先关闭授权再删除实例"
        )
    if await list_file_accesses(db, service_instance_id=instance.id):
        raise FileAccessAuthorizationUnavailable(
            "该实例仍有服务器文件授权，请先移除授权再删除实例"
        )
    await service_catalog_repository.delete_instance(db, instance)


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
