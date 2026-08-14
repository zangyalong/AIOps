# 目标服务器 Alloy 日志采集

Alloy 安装在目标服务器，从已经登记的 systemd 或 Docker 运行对象读取日志并推送中央 Loki。
首次部署不需要手填服务列表：先安装基础组件，AI 发现并注册服务后再生成配置预览，审批通过
才安装配置并启动或重载 Alloy。

统一标签：

```text
service
service_name    # 兼容现有 AIOps 日志查询
environment
server
runtime_type
runtime_name
log_source
```

## 1. 首次基础安装

顶层 `deploy.conf` 保持：

```ini
ENABLE_ALLOY=true
SERVER_NAME=web01
LOKI_PUSH_URL=http://监控服务器:3100/loki/api/v1/push
ALLOY_APT_REPO_URL=https://apt.grafana.com
ALLOY_APT_GPG_KEY_URL=https://apt.grafana.com/gpg-full.key
ALLOY_RPM_REPO_URL=https://mirrors.tuna.tsinghua.edu.cn/grafana/yum/rpm
ALLOY_RPM_GPG_KEY_URL=https://rpm.grafana.com/gpg.key
ALLOY_PACKAGE_PROXY_URL=
ALLOY_SERVICE_MAP=
ALLOW_DOCKER_SOCKET=false
START_ALLOY=false
```

然后执行顶层 `sudo ./deploy.sh`。它会安装 Alloy、journald 读取组、固定生成器和固定配置应用器，
但因为还没有服务映射，不会启动 Alloy。

`LOKI_PUSH_URL` 必须是目标服务器能访问的中央 Loki 地址，不能误写目标机自己的
`localhost:3100`。如果明确需要 Docker stdout/stderr，应由管理员理解权限风险后把
`ALLOW_DOCKER_SOCKET=true`；AI Tool 不能自行改变这项 root 策略。

APT 软件包默认从 Grafana 官方源下载，RPM 软件包默认临时使用清华 TUNA 镜像，并继续通过
Grafana 官方 GPG key 校验 RPM 包。DNF 安装结束或失败后都会恢复服务器原有的 `grafana.repo`。
若服务器只能访问经审核的内网镜像，请在顶层 `deploy.conf` 同时填写对应平台的仓库地址和
GPG 公钥地址（APT 使用 `ALLOY_APT_*`，DNF 使用 `ALLOY_RPM_*`）。
如果只是出口受限，填写 `ALLOY_PACKAGE_PROXY_URL=http://proxy.example.internal:7890` 即可；代理仅用于
这次软件包下载，不会持久化为目标服务器的系统代理。不要把来源不明的公开镜像或 GPG key 写入配置。

## 2. 通过对话发现和注册服务

基础部署完成后对 AIOps 说：

```text
发现 web01 上的服务，并注册到服务目录。
```

AI 先调用只读 `discover_server_services`，再把选中的 systemd、Docker 和长期监听进程提交服务
目录审批。批准后，服务实例会保存稳定的 `runtime_type` / `runtime_name`。

## 3. 预览并审批应用日志配置

继续说：

```text
预览 web01 的日志采集配置。
```

只读 `preview_server_log_config` 根据服务目录自动映射：

| 运行类型 | 自动日志源 | 说明 |
| --- | --- | --- |
| `systemd` | `journal` | 使用准确的 `.service` 单元过滤 journald |
| `docker` | `docker` | 使用稳定容器名过滤 stdout/stderr |
| `process` | `none` | 无法仅凭 PID/命令可靠推断日志文件 |

预览返回结构化 mappings、`services.map` 内容和 SHA-256 摘要，不连接目标服务器。确认后说：

```text
应用刚才预览的 web01 日志配置。
```

高风险写 Tool `apply_server_log_config` 会：

1. 重新读取服务目录，核对摘要和每项映射，变化时拒绝应用并要求重新预览。
2. 使用服务器记录中的非 root `ssh_user` / `ssh_key_filename`；读写共用身份，但权限仍由固定网关隔离。
3. 只把自动生成的映射上传到 `/var/lib/aiops/incoming/services.map`。
4. 只执行 `sudo /usr/local/sbin/aiops-target-deploy apply-alloy`。
5. root 应用器再次校验文件所有者、权限、大小和格式，运行 `alloy validate`，备份旧配置后
   启动或重载 Alloy；失败时尝试恢复旧配置。

自动应用不接受 `file` 日志源，不能指定脚本路径、目标路径或 Loki 地址。

## 4. Docker 与文件日志边界

Docker socket 通常赋予接近 root 的容器控制能力。服务目录中存在 Docker 实例时，预览会提示
需要该权限，但目标服务器只有在管理员首次设置 `ALLOW_DOCKER_SOCKET=true` 后才会实际应用。

未纳管进程、MySQL、PHP、FTP 等文件日志路径无法仅靠进程可靠判断。需要文件日志时，管理员
使用 `services.map.example` 人工准备映射和最小 ACL，然后直接运行安装器：

```bash
sudo ./install.sh \
  --server web01 \
  --loki-url http://监控服务器:3100/loki/api/v1/push \
  --ssh-user aiops \
  --map ./services.map \
  --allow-docker-socket
```

文件目录按实际范围授予 Alloy，不要开放整个 `/var/log`：

```bash
sudo setfacl -R -m u:alloy:rx /var/log/mysql
sudo setfacl -R -d -m u:alloy:rx /var/log/mysql
```

## 5. 安装后的固定文件

```text
/etc/aiops/alloy-target.conf                 # root 确定的服务器/Loki/Docker 策略
/etc/aiops/alloy-services.map                # 最近一次成功应用的服务映射
/etc/alloy/config.alloy                      # Alloy 实际运行配置
/usr/local/libexec/aiops/generate-alloy-config
/usr/local/libexec/aiops/apply-alloy-config
/var/lib/aiops/incoming/                      # 仅 aiops 用户可写的暂存目录
```

## 6. 检查与排错

配置应用成功后：

```bash
sudo ./check.sh
sudo alloy validate /etc/alloy/config.alloy
sudo systemctl status alloy --no-pager
sudo journalctl -u alloy -n 100 --no-pager
```

中央 Loki 可检查：

```logql
{server="web01", service="example-api", environment="prod"}
```

- 提示没有服务映射：先完成服务发现和目录注册。
- 提示预览不一致：服务目录在预览后发生变化，重新预览并审批。
- Docker 未授权：由管理员更新 `ALLOW_DOCKER_SOCKET` 并重新执行基础部署。
- journald 没日志：检查 `id alloy` 是否包含 `adm` 或 `systemd-journal`。
- Alloy 正常但 Loki 没日志：检查目标服务器到中央 Loki 的地址、防火墙和认证代理。
