# AIOMS 目标服务器只读配置包

这个目录放在 **AIOMS 项目仓库** 的 `scripts/target-server/`。安装时把整个目录复制到每台
目标服务器的临时目录，运行安装脚本后即可删除临时副本；真正长期生效的文件会安装到
`/usr/local/sbin/` 和 `/etc/sudoers.d/`。

它只负责目标服务器上的只读命令权限，不包含 SSH 私钥，也不修改 `sshd_config`。

## 文件和安装位置

| 仓库文件 | 目标服务器位置 | 用途 |
| --- | --- | --- |
| `aioms-docker-read` | `/usr/local/sbin/aioms-docker-read` | 校验 Docker 只读子命令和容器名，再调用 Docker |
| `aioms-service-read` | `/usr/local/sbin/aioms-service-read` | 校验 socket 查询类型和纯数字 PID，再调用 `ss`、`ps` 或 `systemctl status` |
| `aioms-readonly.sudoers` | `/etc/sudoers.d/aioms-readonly` | 只允许指定 SSH 用户免密 sudo 执行上面两个网关 |
| `install.sh` | 不长期安装 | 检查用户名、安装脚本、生成 sudoers 并用 `visudo` 校验 |
| `README.md` | 不长期安装 | 安装、参数、安全边界和排错说明 |

两个网关必须保持 `root:root`、权限 `0755`，sudoers 必须保持 `root:root`、权限 `0440`。
AIOMS 的 SSH 用户只能执行它们，不能修改它们。

## 前置条件

- 目标服务器使用 Linux，并且有 Bash、sudo、`visudo`、systemd 和 `ss`（iproute2）。
- AIOMS 使用的 SSH 用户已经存在，例如 `aioms`。
- Docker 是可选的；没安装 Docker 时只有 Docker 盘点失败，systemd 和 socket 盘点仍可使用。
- 不要把 AIOMS SSH 用户加入可修改 `/usr/local/sbin` 或 `/etc/sudoers.d` 的组。

## 安装

先从运行 AIOMS 项目的机器复制目录：

```bash
scp -r scripts/target-server root@目标服务器:/tmp/aioms-target-server
```

再登录目标服务器安装。最后一个参数是 `servers.ssh_user` 中配置的 SSH 用户名：

```bash
ssh root@目标服务器
cd /tmp/aioms-target-server
./install.sh aioms
```

如果使用普通管理员账号：

```bash
sudo ./install.sh aioms
```

安装脚本不会创建用户、修改 SSH 公钥或开放任意 sudo。再次运行会更新同名网关和 sudoers，
适合以后把新版本同步到多台服务器；更新前应先在一台测试服务器验证。

## 安装后验证

以下命令都在目标服务器执行。

确认文件所有者和权限：

```bash
ls -l /usr/local/sbin/aioms-docker-read
ls -l /usr/local/sbin/aioms-service-read
ls -l /etc/sudoers.d/aioms-readonly
visudo -cf /etc/sudoers.d/aioms-readonly
```

确认 SSH 用户获得的 sudo 权限：

```bash
sudo -l -U aioms
```

验证 TCP/UDP 和 Unix Socket 盘点：

```bash
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-service-read inet-sockets
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-service-read unix-sockets
```

从第一条结果选一个真实 PID，例如 `12345`，再验证定点查询：

```bash
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-service-read process 12345
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-service-read unit 12345
```

有 Docker 时验证业务资产格式：

```bash
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-docker-read inventory
```

最后确认非法参数会被拒绝：

```bash
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-service-read process abc
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-docker-read logs '--since=24h'
```

两条都应该报参数错误，不能执行底层命令。

## `aioms-docker-read` 参数

| 参数 | 实际执行 | 用途 |
| --- | --- | --- |
| `ps` | `docker ps` | 查看运行中容器 |
| `ps-all` | `docker ps -a` | 查看全部容器 |
| `inventory` | 固定 `docker ps --format ...` | 给服务发现返回名称、镜像、状态和端口 |
| `images` | `docker images` | 查看镜像 |
| `stats` | `docker stats --no-stream` | 查看一次性资源使用 |
| `system-df` | `docker system df` | 查看 Docker 磁盘占用 |
| `version` | `docker version` | 查看客户端和服务端版本 |
| `network-ls` | `docker network ls` | 查看网络 |
| `volume-ls` | `docker volume ls` | 查看数据卷 |
| `top <容器名>` | `docker top <容器名>` | 查看指定容器进程 |
| `logs <容器名>` | `docker logs --tail 100 <容器名>` | 只读取指定容器最后 100 行日志 |

`<容器名>` 只允许字母或数字开头，后面只能有字母、数字、点、下划线和连字符；不能传 Docker
选项、空格、管道、重定向或第二个参数。

## `aioms-service-read` 参数

| 参数 | 实际执行 | 用途 |
| --- | --- | --- |
| `inet-sockets` | `ss -H -lntup` | 查询 TCP/UDP 监听端口及 PID |
| `unix-sockets` | `ss -H -lxnp` | 查询 Unix Socket，例如 PHP-FPM |
| `process <PID>` | `ps -p <PID> -o pid,ppid,user,lstart,cmd` | 人工调查一个陌生 PID |
| `processes <PID...>` | 对最多 100 个 PID 执行一次定点 `ps` | 服务发现内部批量补充运行时间、unit 和命令 |
| `unit <PID>` | `systemctl status <PID> --no-pager` | 判断 PID 是否属于 systemd unit |

PID 只能是 1 到 10 位正整数。`processes` 虽然接受多个 PID，但每个参数都要通过相同校验，
且最多 100 个；它不能变成任意 `ps` 参数。

`ss` 的参数固定写在网关内部，尤其不会开放可关闭 socket 的 `ss -K`。

## sudoers 为什么只写网关路径

sudoers 中没有给 `/usr/bin/docker`、`/usr/sbin/ss`、`/usr/bin/ps` 或任意 shell 授权，只允许：

```text
/usr/local/sbin/aioms-docker-read
/usr/local/sbin/aioms-service-read
```

sudoers 对未写参数的命令路径会允许向网关传参数，因此安全边界在两个 root 所有的网关内部：
它们使用 `case` 枚举子命令，检查参数个数、容器名或 PID，拒绝所有未列出的输入。正因为如此，
绝不能让 `aioms` 用户拥有网关文件或其上级目录的写权限。

## 与已有 sudoers 共存

如果服务器已经有 `/etc/sudoers.d/aioms-docker-read`，新文件可以暂时与它共存。确认 AIOMS
的新扫描和 Docker 查询都正常后，再由管理员决定是否把旧规则合并或删除；不要在没有验证时
直接删除原配置。

## SSH 公钥和服务器指纹放在哪里

- AIOMS 登录公钥放在目标用户的 `~/.ssh/authorized_keys`，每台服务器按现有 SSH 流程配置。
- AIOMS 后端使用的私钥放在项目根目录 `ssh/`，数据库只保存私钥文件名。
- 服务器主机公钥（host key）应记录在运行 AIOMS 后端那台机器的 `known_hosts`，不放在目标
  服务器的这个配置包里。目标服务器可用
  `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` 从可信控制台核对指纹。

## AIOMS 的标准业务资产扫描

安装完成后，`discover_server_services` 按下面顺序工作：

1. `systemctl list-units`：盘点 systemd 管理的数据库、代理、监控和第三方服务。
2. `aioms-docker-read inventory`：盘点 Docker 容器的名称、镜像、状态和端口。
3. `aioms-service-read inet-sockets`：寻找 TCP/UDP 服务。
4. `aioms-service-read unix-sockets`：补充 PHP-FPM 等本机 socket 服务。
5. 从 socket 输出提取纯数字 PID，只调用 `processes <PID...>` 补充对应进程，不读取整机进程表。

机器发现的是“运行对象”，不一定等于业务服务。Agent 会在审批卡中列出
`逻辑服务 ← 运行类型/运行对象` 的映射，只有人工批准后才写入服务目录。
