# 本地 SSH 测试沙箱

用 Docker 在 macOS 上起一个装了 SSH server 的轻量 Linux 容器，把它注册进 `servers` 表，
然后在 AIOMS 对话界面里让 `run_diagnostic_command` 真的通过 SSH 连上去执行命令——而不是
之前那种写死的模拟结果（见 `app/agents/tools/commands.py`）。

**为什么用容器测，不直接连真实服务器**：这个工具本质是"AI 决定要不要执行一条命令"，
碰的是 CLAUDE.md 安全红线那条"SSH/Shell 工具要走白名单命令"。容器是随时可以
`docker rm` 扔掉的沙箱，配置错了、命令跑飞了，代价也就是重新起一个容器，风险比一上来
就对着真实服务器验证低得多。

## 前置条件

- Docker Desktop for Mac 已经装好并且在运行，用这条确认：
  ```bash
  docker info
  ```
  报错连不上就先把 Docker Desktop 打开。
- 本地的 `aioms` 数据库和后端能正常跑起来（`servers` 表已经建好，参考 CLAUDE.md「常用命令」）。

## 第 1 步：生成一对测试用的 SSH 密钥

```bash
cd /path/to/AIOMS   # 仓库根目录，跟 CLAUDE.md 同一层
ssh-keygen -t ed25519 -f ssh/aioms-test -N "" -C "aioms-test-sandbox"
```

`-N ""` 是不给私钥设密码，图测试方便；真实服务器的私钥不要这样搞。执行完 `ssh/` 目录下
会多两个文件：

- `ssh/aioms-test`（私钥，`ssh-keygen` 生成的默认权限就是 600，`.gitignore` 已经把整个
  `ssh/` 目录挡在仓库外了，放心用）
- `ssh/aioms-test.pub`（公钥）

## 第 2 步：起容器

用 [linuxserver/openssh-server](https://docs.linuxserver.io/images/docker-openssh-server/)
这个镜像——它专门做"容器里跑一个开箱即用的 SSH server"这件事，不用自己去装
openssh-server、写 sshd_config。

```bash
docker run -d \
  --name aioms-test-server \
  -p 2222:2222 \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=Asia/Shanghai \
  -e USER_NAME=opsuser \
  -e PUBLIC_KEY_FILE=/keys/aioms-test.pub \
  -v "$(pwd)/ssh:/keys:ro" \
  lscr.io/linuxserver/openssh-server:latest
```

- `-p 2222:2222`：容器内部固定监听 2222 端口（这个镜像写死的），映射到 Mac 本机的 2222 端口
- `-v "$(pwd)/ssh:/keys:ro"`：把仓库的 `ssh/` 目录只读挂进容器，容器启动时从
  `/keys/aioms-test.pub` 读公钥、自动写进 `authorized_keys`
- 默认关掉密码登录和 sudo（镜像的 `PASSWORD_ACCESS`/`SUDO_ACCESS` 默认都是 `false`），
  只能用密钥登录——这次也用不上 sudo，白名单命令都是只读诊断命令

跑起来后确认容器状态、看看有没有启动报错：

```bash
docker ps --filter name=aioms-test-server
docker logs aioms-test-server
```

## 第 3 步：先脱离 AIOMS，手动 SSH 验证链路通不通

这一步很重要——先确认"Docker 容器 + 网络 + 密钥"这条链路本身是通的，后面 AIOMS 里
连不上时能立刻判断是这一层的问题还是代码的问题。

```bash
ssh -i ssh/aioms-test -p 2222 \
  -o UserKnownHostsFile=/dev/null \
  -o StrictHostKeyChecking=no \
  opsuser@127.0.0.1
```

加了 `-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no` 是刻意跳过 host key
校验——这个镜像没有持久化 host key（没挂 `/config` 卷），**每次重新起容器 host key
都是新生成的**，用普通 `ssh` 命令会因为"跟上次记的指纹不一样"被拒绝连接
（`REMOTE HOST IDENTIFICATION HAS CHANGED`）。对一次性本地沙箱来说这个校验意义不大
（流量根本没出这台 Mac），所以直接跳过，也跟 `commands.py` 里 `asyncssh.connect(...,
known_hosts=None)` 保持一致——**这个跳过只对这种一次性沙箱成立，连真实/生产服务器
不能这样**。

能登进去、跑一下 `uptime` 看到正常输出，就说明这一层没问题，`exit` 退出来继续下一步。

## 第 4 步：把测试容器注册进 servers 表

后端要先跑起来（默认 `http://localhost:8010`），直接用现成的 servers API：

```bash
curl -X POST http://localhost:8010/servers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "docker-test-01",
    "ip_address": "127.0.0.1",
    "description": "本地 Docker SSH 测试沙箱",
    "ssh_port": 2222,
    "ssh_user": "opsuser",
    "ssh_key_filename": "aioms-test"
  }'
```

- `ip_address` 填 `127.0.0.1`：AIOMS 后端是跑在 Mac 本机的（不是在 Docker 里），它连的是
  "本机 2222 端口"，Docker 已经把容器的 2222 映射到这里了
- `ssh_key_filename` 只填文件名 `aioms-test`（不带路径、不带 `.pub`），后端会自动拼成
  `settings.ssh_key_dir / "aioms-test"` 去找私钥（也就是 `ssh/aioms-test`）

## 第 5 步：在对话界面里真的用一下

打开 AIOMS 前端，问类似这样的问题：

> 帮我看看 docker-test-01 这台服务器现在的负载和内存情况

正常的话 AI 会调用 `run_diagnostic_command`，后端日志里能看到真实的 SSH 执行过程，
回答里会带上"（真实执行于 docker-test-01）"这样的字样和真实命令输出，不再是固定的模拟文本。

这是纯读诊断工具，因此不会弹人工审批卡片。当前沙箱镜像默认关闭 sudo，也不运行 systemd，
不能用它验证 systemd 服务控制写工具；写工具应在专门的、可随时重建的 systemd 测试机上验收。

## 写工具验收说明

`start_service`、`stop_service`、`restart_service` 与上面的读诊断工具走不同边界：模型提出
操作后，LangGraph 会先挂起并等待人工批准；批准后，工具使用同一非 root AIOMS 身份调用固定网关，且
AIOMS 与目标服务器两端都检查精确白名单。例如在专用测试机上允许控制 nginx：

```dotenv
SSH_SERVICE_CONTROL_ALLOWLIST=nginx.service
```

目标测试账号还必须能非交互执行 `sudo -n -- systemctl restart -- nginx`。不要在生产服务器上直接
做首次验收；当前项目尚未完成鉴权、RBAC、主机指纹校验和不可篡改审计日志。

## 排错

| 现象 | 大概率原因 |
| --- | --- |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` / `Host key verification failed` | 容器 host key 每次重启都会变，`~/.ssh/known_hosts` 里缓存的是旧指纹。先 `ssh-keygen -R "[127.0.0.1]:2222"` 清掉旧缓存，再用第 3 步里带 `-o StrictHostKeyChecking=no` 的命令连接，以后就不会再撞上这个问题 |
| `Permissions 0644 for 'ssh/aioms-test' are too open` | 私钥权限不对：`chmod 600 ssh/aioms-test` |
| `Permission denied (publickey)` | 公钥没挂对，检查 `docker logs aioms-test-server` 有没有报错，或确认 `.pub` 文件内容完整 |
| `Connection refused` | 容器没起来或端口没映射对，`docker ps` 看一下状态 |
| 工具回答"没有配置 SSH 连接信息" | servers 表这条记录的 `ssh_user`/`ssh_key_filename` 没填对，回第 4 步检查 |
| 某个白名单命令报 "command not found" | 这个精简镜像里没装这个命令，属于正常情况——说明连接和执行链路本身是通的，只是这个命令这个镜像没有；真实服务器工具链更全一般不会有这个问题 |

## 关于安全的两个提醒

- `app/agents/tools/commands.py` 里连接时传的是 `known_hosts=None`，也就是不校验目标主机
  的身份——这对一次性的本地测试沙箱没问题（流量根本没出这台 Mac），但**绝对不能带着这个设置去连真实/生产服务器**，那样谁都能冒充目标主机。真要接真实服务器，要么提前把
  host key 指纹加进 known_hosts，要么显式传一个真实的 `known_hosts` 路径。
- 读诊断白名单目前硬编码在 `_ALLOWED_COMMANDS` 里，只有 `uptime` / `df -h` / `free -m` 三个
  只读命令。想加新命令要改代码、过一遍是不是真的"只读、无害"，不要为了图方便把白名单
  放开成任意命令。
- 写工具不能复用读诊断命令入口。新增写工具必须注册到 `catalog.py` 的写工具目录，声明风险
  等级和审批内容，并在工具内部继续校验允许执行的具体对象。

## 清理

```bash
docker stop aioms-test-server
docker rm aioms-test-server
```

容器删了之后 `servers` 表里 `docker-test-01` 这条记录还在（只是连不上了），要一起清掉调用
`DELETE /servers/{id}`，或者留着下次重新起容器时继续用。
