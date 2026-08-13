# AIOps 服务发现只读网关

本目录负责目标服务器的业务资产发现权限，由根目录 `deploy.sh` 自动调用。也可以单独安装：

```bash
sudo ./install.sh aiops
```

其中 `aiops` 是 AIOps 后端用于 SSH 登录目标服务器的现有普通用户。安装器不会创建用户、
写入 SSH 公钥或修改 `sshd_config`。

## 安装结果

| 源文件 | 目标位置 | 权限 | 用途 |
| --- | --- | --- | --- |
| `aiops-service-read` | `/usr/local/sbin/aiops-service-read` | `root:root 0755` | 查询监听 socket、指定 PID 和对应 systemd unit |
| `aiops-docker-read` | `/usr/local/sbin/aiops-docker-read` | `root:root 0755` | 执行固定的 Docker 只读盘点命令 |
| `aiops-readonly.sudoers` | `/etc/sudoers.d/aiops-readonly` | `root:root 0440` | 只允许指定 SSH 用户免密执行上述两个网关 |

sudoers 不直接授权任意 `docker`、`ss`、`ps`、`systemctl` 或 Shell。两个网关内部使用固定
子命令，并校验容器名、参数数量和纯数字 PID。

## 安装后检查

```bash
sudo visudo -cf /etc/sudoers.d/aiops-readonly
sudo -l -U aiops
sudo -u aiops sudo -n -- /usr/local/sbin/aiops-service-read inet-sockets
sudo -u aiops sudo -n -- /usr/local/sbin/aiops-service-read unix-sockets
sudo -u aiops sudo -n -- /usr/local/sbin/aiops-docker-read inventory
```

没有安装 Docker 时最后一条会失败，不影响 systemd、TCP/UDP、Unix Socket 和监听进程发现。
