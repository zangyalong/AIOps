from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Server(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "servers"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 三个都可空：不是每台 server 都要能真的 SSH 上去，纯记录用的服务器可以不填。
    # 都填了才会被当成"可执行白名单命令"的目标，见 app/agents/tools/commands.py。
    ssh_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ssh_user: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 只存文件名，不存路径——实际私钥路径是 settings.ssh_key_dir / ssh_key_filename 拼出来的，
    # 密钥内容本身放仓库根目录 ssh/ 下，不进数据库、不进仓库。
    ssh_key_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
