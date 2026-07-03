"""ORM 基础模型与通用 Mixin。

提供：
- ``Base``: 所有模型继承的 DeclarativeBase，是全项目唯一的一个（`app.main` 启动时
  ``Base.metadata.create_all()`` 也是靠它拿到全部表）——不要在别处再定义一个新的
  DeclarativeBase 子类，否则模型会注册到不同的 metadata 上，启动建表会看不到那些表。
- ``UUIDMixin``: UUID 主键
- ``TimestampMixin``: ``created_at`` / ``updated_at``
"""

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的根基类。"""

    type_annotation_map: ClassVar[dict] = {
        uuid.UUID: PG_UUID(as_uuid=True),
    }


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
