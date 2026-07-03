from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Server(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "servers"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
