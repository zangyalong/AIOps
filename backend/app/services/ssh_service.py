"""SSH 连接的公共逻辑，run_whitelisted_command 工具和"测试连接"接口都走这里，
避免同一段连接参数拼装代码在两个地方各写一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.models.server import Server


class SSHNotConfigured(Exception):
    """服务器没有配置 SSH 连接信息（ssh_user / ssh_key_filename）。"""


class SSHKeyMissing(Exception):
    """配置的密钥文件名在 settings.ssh_key_dir 里找不到对应文件。"""

    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path
        super().__init__(f"密钥文件不存在：{key_path}")


@dataclass
class SSHConnectParams:
    host: str
    port: int
    username: str
    client_keys: list[str]


def build_connect_params(server: Server) -> SSHConnectParams:
    """校验 server 是否配置了 SSH 信息、密钥文件是否存在，返回 asyncssh.connect() 需要的参数。

    不在这里直接发起连接——调用方（工具 / 测试连接接口）各自决定连上之后要做什么、
    超时设多少，这里只管"连接参数对不对"。
    """
    if not (server.ssh_user and server.ssh_key_filename):
        raise SSHNotConfigured
    key_path = settings.ssh_key_dir / server.ssh_key_filename
    if not key_path.is_file():
        raise SSHKeyMissing(key_path)
    return SSHConnectParams(
        host=server.ip_address,
        port=server.ssh_port or 22,
        username=server.ssh_user,
        client_keys=[str(key_path)],
    )
