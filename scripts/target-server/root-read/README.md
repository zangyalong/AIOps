# AIOMS root 只读沙箱

这个入口解决的是“诊断搜索被命令白名单卡住”的问题。AIOMS 仍以普通 `aioms` 用户 SSH
登录，只能无参数调用 root 持有的 `/usr/local/sbin/aioms-root-read`；命令通过 stdin JSON
进入一次性 Docker 容器，不会在宿主机 Shell 中被 `eval`。后端可选择宿主机语境或工具箱语境，
这个选择属于内部实现，不交给 Agent 或用户判断。

## 边界

- 宿主机语境先进入只读挂载的宿主根，再立即收回临时 `SYS_CHROOT` capability；此时 `/`、PATH 和可执行程序都属于目标服务器，可用于核验真实 OS、软件版本和文件。
- 工具箱语境用于内部调用 Debian 镜像里的 `rg/find/jq` 等诊断工具，宿主机文件根为 `/host`。该语境不会作为 Agent 可见的“模式”暴露。
- 宿主机根文件系统以“只读且不递归”方式挂到 `/host`；root 持有的解析器再枚举安全子挂载并逐个只读挂入。这样不会在旧内核上把子挂载意外暴露为可写，也仍能读取独立业务数据盘。容器根目录同样只读，只有容器自己的 `/tmp` 是受限临时盘。
- 容器没有网络、Docker socket、宿主机 `/run`、`/tmp`、`/proc`、`/sys` 或 `/dev`，不能控制
  systemd、Docker 或宿主进程。
- 私钥、云凭据、账号哈希、宿主机 `/tmp`、数据库数据目录、容器运行时数据和 AIOMS 部署凭据会被标记挂载遮蔽；目录中出现 `AIOMS_PATH_MASKED.txt` 或文件内容为 `AIOMS_PATH_MASKED` 表示无法读取，绝不能解释为真实为空或不存在。普通系统配置仍可用于诊断。
- 镜像由安装器构建；启动器只读取 `/etc/aioms/root-read.image-id` 中的不可变 image ID，
  调用方不能指定镜像、挂载、网络、capability 或 Docker 参数。
- 单条命令最长 8192 字符、最长运行 20 秒、最多返回 64 KiB；外层 30 秒还会强制删除容器。
- 输出会做常见密码、Token、Authorization、URL 凭据和 PEM 私钥脱敏。但任意 root 读取能力
  无法从数学上保证识别所有业务秘密，新增凭据目录时必须同步加入遮蔽清单。

这个沙箱不替代现有结构化运行时探针：`systemctl`、Docker 容器状态和 socket/PID 发现仍走
固定只读网关；普通宿主机文件、OS 和程序版本则可直接在宿主机只读语境中核验。真正写操作
仍只能调用已登记写工具，经过人工审批后进入固定写网关。

## 协议

```json
{"command":"python --version","timeout":20,"scope":"host"}
```

返回：

```json
{"exit_code":0,"timed_out":false,"truncated":false,"output":"..."}
```

## 安装

由总入口自动调用，也可单独执行：

```bash
sudo ./install.sh aioms
```

首次安装需要 Docker 和 `/usr/bin/python3.11` 可用；Alibaba Cloud Linux 3 可先执行
`sudo dnf install -y python3.11`。宿主机挂载解析器固定使用 Python 3.11，不会替换系统默认
Python 3.6。Debian 基础镜像默认通过 DaoCloud 国内代理
`m.daocloud.io/docker.io/library/debian:bookworm-slim` 拉取，只影响本次镜像构建，不修改目标机
Docker daemon 的全局镜像源。镜像内部较大的 Debian 主仓库索引通过清华 TUNA 拉取，安全更新
仍保留 Debian 官方源；APT 构建阶段设置 3 次重试、30 秒连接超时，并关闭 HTTP Pipelining，
减少国内网络抖动造成的长时间停顿。镜像安装完整的 Debian `python3` 标准库，并在构建阶段验证
入口所需模块，避免最小解释器缺少 `json`。后续内容未变化时直接复用已固定的本地 image ID，
不会重复构建。`bind-recursive=disabled` 要求 Docker Engine 25+；安装器会明确检查。Linux 5.10
可用，因为每个安全子挂载都独立设为只读，不依赖 Linux 5.12 才提供的递归只读。

## 安装后验证

下面命令都从目标服务器执行：

```bash
# 应成功
printf '%s' '{"command":"cat /etc/hosts; python --version","timeout":10,"scope":"host"}' \
  | sudo -u aioms sudo -n -- /usr/local/sbin/aioms-root-read

printf '%s' '{"command":"rg -n redis /host/etc /host/www 2>/dev/null","timeout":20,"scope":"toolbox"}' \
  | sudo -u aioms sudo -n -- /usr/local/sbin/aioms-root-read

# 宿主机写入应失败
printf '%s' '{"command":"touch /etc/aioms-test","timeout":10,"scope":"host"}' \
  | sudo -u aioms sudo -n -- /usr/local/sbin/aioms-root-read

# 网络与控制面应失败
printf '%s' '{"command":"systemctl restart nginx; python3 -c '\''import socket; socket.create_connection((\"example.com\", 443))'\''","timeout":10,"scope":"host"}' \
  | sudo -u aioms sudo -n -- /usr/local/sbin/aioms-root-read
```

还应确认 `/host/run`、`/host/root/.ssh`、`/host/etc/ssh/ssh_host_*_key`、`/host/var/lib/docker` 等位置不可读取，且
`docker inspect` 看不到网络、额外挂载或高权限 capability。
