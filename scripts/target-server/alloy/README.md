# web01 Alloy 日志采集快速配置

这套文件安装在目标 Linux 服务器上。Alloy 从登记过的 systemd、Docker 或文件日志读取数据，
添加 AIOMS 统一标签后，主动推送到中央 Loki；它不会安装 Prometheus、Loki 或 Grafana。

统一标签是：

```text
service
service_name    # 迁移期兼容现有 AIOMS search_logs
environment
server
runtime_type
runtime_name
log_source
```

## 先确认网络

`--loki-url` 必须是 **web01 能访问到的中央 Loki push URL**：

```text
http://监控服务器内网地址:3100/loki/api/v1/push
```

不能写 `localhost:3100`，因为在 web01 上它表示 web01 自己。当前 AIOMS Docker Compose 的
Loki 没有内建认证，不要把 3100 直接暴露到公网；测试时至少用防火墙只允许 web01，正式使用
应在 Loki 前放带 TLS/鉴权的反向代理。

## 1. 准备服务映射

```bash
cd /tmp/aioms-target-server/alloy
cp services.map.example services.map
vi services.map
```

一行对应一个服务实例：

```text
service|environment|runtime_type|runtime_name|log_source|log_path
```

| log_source  | 适用场景                           | 填法                                      |
| ----------- | ---------------------------------- | ----------------------------------------- |
| `journal` | systemd 服务写 journald            | `runtime_name` 填准确的 `.service` 名 |
| `docker`  | Docker stdout/stderr               | `runtime_name` 填容器名，不填容器 ID    |
| `file`    | MySQL/PHP/FTP 或未纳管进程文件日志 | `log_path` 填绝对路径或 glob            |
| `none`    | 先登记但暂不采集日志               | 不生成日志源                              |

服务名、环境、运行类型和运行对象应与 AIOMS 服务目录完全一致。不要把 PID、容器 ID 或请求
ID 填成稳定身份。

## 2. 只生成配置预览

这一步不需要 root，也不会修改 `/etc/alloy`：

```bash
./generate-config.sh \
  ./services.map \
  web01 \
  http://监控服务器:3100/loki/api/v1/push \
  /tmp/web01.config.alloy

less /tmp/web01.config.alloy
```

如果服务器已经安装 Alloy，可以进一步做语法检查：

```bash
alloy validate /tmp/web01.config.alloy
```

## 3. 安装

只有 systemd 和文件日志时：

```bash
sudo ./install.sh \
  --server web01 \
  --loki-url http://监控服务器:3100/loki/api/v1/push \
  --map ./services.map
```

映射中包含 Docker 日志时，必须显式确认 Docker socket 权限：

```bash
sudo ./install.sh \
  --server web01 \
  --loki-url http://监控服务器:3100/loki/api/v1/push \
  --map ./services.map \
  --allow-docker-socket
```

Docker Unix socket 不是普通“只读日志权限”；能访问它的用户通常具有接近 root 的容器控制
能力。这个参数只表示你已经理解并接受该风险，脚本不会静默授权。

想先安装文件但不启动：

```bash
sudo ./install.sh \
  --server web01 \
  --loki-url http://监控服务器:3100/loki/api/v1/push \
  --map ./services.map \
  --allow-docker-socket \
  --no-start
```

安装器支持 Debian/Ubuntu 的 `apt` 和 RHEL/Fedora 的 `dnf`。它会：

1. 从 Grafana 官方仓库安装 Alloy（已经安装则跳过）。
2. 给 `alloy` 用户添加 journald 读取组。
3. 在显式允许时添加 Docker socket 所属组。
4. 生成并执行 `alloy validate`。
5. 备份已有 `/etc/alloy/config.alloy`。
6. 安装新配置并启动或重启 `alloy.service`。

## 4. 文件日志权限

安装器不会自动扩大任意日志目录权限。对每个 `file` 日志源，以实际目录为单位授权，例如：

```bash
sudo setfacl -R -m u:alloy:rx /var/log/mysql
sudo setfacl -R -d -m u:alloy:rx /var/log/mysql
sudo systemctl restart alloy
```

目录需要 `x` 权限才能遍历，文件需要 `r` 权限。不要直接把整个 `/var/log` 开放给 Alloy。

## 5. 检查

```bash
sudo ./check.sh
```

然后在能访问中央 Loki 的机器检查统一标签：

```bash
curl -fsS http://localhost:3100/loki/api/v1/label/service/values
curl -fsS http://localhost:3100/loki/api/v1/label/server/values
```

AIOMS 对话验证示例：

```text
查询 pixel-forge-backend 最近 10 分钟日志
```

## 排错

```bash
sudo alloy validate /etc/alloy/config.alloy
sudo systemctl status alloy --no-pager
sudo journalctl -u alloy -n 100 --no-pager
id alloy
getent group systemd-journal
ls -l /var/run/docker.sock
```

- Alloy 正常但 Loki 没日志：检查 web01 到 Loki 的地址、防火墙和 push URL。
- journald 没日志：检查 `alloy` 是否在 `adm`/`systemd-journal` 组，以及 unit 名是否准确。
- Docker 报 permission denied：检查 socket 所属组和 `id alloy`，然后重启 Alloy。
- 文件日志 permission denied：给具体日志目录设置 ACL，不要改成全局可读。
- `process` 服务没有日志：仅发现进程和端口无法推断日志路径；需要手工填写 `file` 路径，
  或把重要进程改为 systemd 后采集 journald。
