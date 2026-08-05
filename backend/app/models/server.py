from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Server(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "servers"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 日常连接三个字段都可空：不是每台 server 都要能真的 SSH 上去，纯记录可以不填。
    # 都填了才会被当成"可执行白名单命令"的目标，见 app/agents/tools/commands.py。
    ssh_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ssh_user: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 只存文件名，不存路径——实际私钥路径是 settings.ssh_key_dir / ssh_key_filename 拼出来的，
    # 密钥内容本身放仓库根目录 ssh/ 下，不进数据库、不进仓库。
    ssh_key_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 服务器写 Tool 使用独立部署身份，不能复用上面的日常只读密钥。SSH 端口与主机地址仍
    # 复用本服务器记录；只有经过审批的部署 Tool 会读取这两个字段。
    deploy_ssh_user: Mapped[str | None] = mapped_column(String(50), nullable=True)
    deploy_ssh_key_filename: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    # 最近一次"测试连接"的结果，持久化下来，刷新页面不丢：
    # unknown（还没测过）/ active / unreachable / unconfigured
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown"
    )

    # 这台服务器在监控系统里对应的标签，供关联工具（correlate_incident）把业务名翻译成监控标签用，
    # 形如 {"instance": "node-exporter:9100", "service": "aioms-fake-app"}：
    # instance 用来查 Prometheus 指标 / Alertmanager 告警，service 用来查 Loki 日志。可空。
    # 注：create_all 不会给已有表加列，新增这列要手动 ALTER（见 scripts/seed_monitor_labels.py）。
    monitor_labels: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 目标服务器首次部署的非敏感参数。数据库 exporter 密码不会写入这里，生成安装包时
    # 由前端一次性提交并只写入临时目录，响应结束后清理。
    deployment_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
