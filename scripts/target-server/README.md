# AIOMS 目标服务器配置指南

把整个 `scripts/target-server/` 目录复制到目标 Linux 服务器，准备配置文件后执行
一次 `sudo ./deploy.sh`，即可完成：

1. 安装 AIOMS 服务发现所需的只读命令网关和精确 sudoers 权限。
2. 按需安装 Grafana Alloy，采集本机 systemd、Docker 或指定文件日志并推送中央 Loki。
3. 按独立开关部署 node_exporter、cAdvisor、MySQL、PostgreSQL 和 Redis exporter。

## 先分清目标服务器和中央监控服务器

| 位置                      | 本配置包负责                                                | 本配置包不负责                                                                |
| ------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------- |
| 目标服务器（如`web01`） | 只读发现网关、sudoers、可选 Alloy、可选 Prometheus exporter | 不创建 SSH 用户，不配置 SSH 私钥/公钥，不自动创建数据库监控账号，不修改防火墙 |
| 中央监控服务器            | 无                                                          | Loki、Prometheus、Alertmanager、Grafana 仍按中央监控栈单独部署                |

Alloy 和全部 exporter 都是可选组件。只做服务发现时关闭所有采集开关；需要日志时启用
Alloy，需要哪类指标就单独启用对应 exporter。

## 目录说明

```text
target-server/
├── deploy.sh                    # 一键部署总入口
├── deploy.conf.example          # 每台服务器的部署参数模板
├── discovery/
│   ├── install.sh               # 只读发现网关安装器，由 deploy.sh 调用
│   ├── aioms-service-read       # socket、指定 PID 和 systemd unit 只读查询网关
│   ├── aioms-docker-read        # Docker 只读查询网关
│   ├── aioms-readonly.sudoers   # 最小 sudo 权限模板
│   └── README.md                # 发现命令与权限边界
├── alloy/
    ├── install.sh               # Alloy 安装器，由 deploy.sh 按需调用
    ├── generate-config.sh       # 根据服务映射生成 Alloy 配置
    ├── check.sh                 # Alloy 安装后检查
    ├── services.map.example     # 服务与日志来源映射模板
    └── README.md                # Alloy 字段、权限和排错细节
└── metrics/
    ├── install.sh               # 可选 exporter 的 Docker Compose 安装器
    ├── check.sh                 # 容器和 /metrics 端点检查
    ├── *password.example        # 数据库密码文件模板
    ├── mysql-exporter.my.cnf.example
    ├── prometheus-scrape.example.yml
    └── README.md                # 数据库账号、权限、端口和中央抓取说明
```

长期生效的文件会安装到 `/usr/local/sbin/`、`/etc/sudoers.d/`、`/etc/alloy/` 和
`/etc/aioms-exporters/`。上传到临时目录的配置包在验证完成后可以删除。

## 一、部署前准备

目标服务器需要满足：

- Linux 使用 systemd，并有 Bash、sudo、`visudo` 和 `ss`（iproute2）。
- AIOMS 用于 SSH 登录的普通用户已经存在，例如 `aioms`。
- 该用户的 SSH 公钥已经按现有流程写入 `~/.ssh/authorized_keys`。
- 启用 Alloy 时，目标服务器能访问中央 Loki 的 HTTP 推送地址。
- Docker 是可选的；启用任一指标 exporter 时必须已有 Docker 和 Docker Compose。

部署脚本不会创建 SSH 用户、写入公钥、修改 `sshd_config`，也不保存任何密码或私钥。

## 二、在打包前填写配置

在项目机器的 `scripts/target-server/` 中执行：

```bash
cp deploy.conf.example deploy.conf
cp alloy/services.map.example alloy/services.map
```

第二行只在 `ENABLE_ALLOY=true` 时需要。数据库 exporter 的凭据文件也只复制已启用的组件：

```bash
cp metrics/mysql-exporter.my.cnf.example metrics/mysql-exporter.my.cnf
cp metrics/postgres-exporter.password.example metrics/postgres-exporter.password
cp metrics/redis-exporter.password.example metrics/redis-exporter.password
```

编辑 `deploy.conf`：

```ini
AIOMS_SSH_USER=aioms
ENABLE_ALLOY=true
SERVER_NAME=web01
LOKI_PUSH_URL=http://监控服务器:3100/loki/api/v1/push
ALLOY_SERVICE_MAP=alloy/services.map
ALLOW_DOCKER_SOCKET=true
START_ALLOY=true
ENVIRONMENT=prod

ENABLE_NODE_EXPORTER=true
ENABLE_CADVISOR=true
ENABLE_MYSQL_EXPORTER=false
ENABLE_POSTGRES_EXPORTER=false
ENABLE_REDIS_EXPORTER=false
START_EXPORTERS=true
```

| 配置项                             | 何时需要                | 说明                                                             |
| ---------------------------------- | ----------------------- | ---------------------------------------------------------------- |
| `AIOMS_SSH_USER`                 | 始终必填                | 已存在的目标服务器 SSH 用户，应与 AIOMS`servers.ssh_user` 一致 |
| `ENABLE_ALLOY`                   | 始终必填                | `true` 安装日志采集，`false` 只安装发现网关                  |
| `SERVER_NAME`                    | 启用 Alloy              | 稳定服务器标签，如`web01`                                      |
| `LOKI_PUSH_URL`                  | 启用 Alloy              | 从目标服务器可访问的中央 Loki 地址                               |
| `ALLOY_SERVICE_MAP`              | 启用 Alloy              | 相对本目录或绝对路径的服务映射文件                               |
| `ALLOW_DOCKER_SOCKET`            | 采集 Docker 日志        | 映射包含`log_source=docker` 时必须显式设为 `true`            |
| `START_ALLOY`                    | 启用 Alloy              | `false` 时只安装并校验配置，不启动/重启 Alloy                  |
| `ENVIRONMENT`                    | 启用任一 exporter       | Prometheus 统一环境标签，如`prod`                              |
| `ENABLE_NODE_EXPORTER`           | 可选                    | 宿主机 CPU、内存、磁盘和网络，端口 9100                          |
| `ENABLE_CADVISOR`                | 可选                    | Docker 容器资源指标，端口 8080                                   |
| `ENABLE_MYSQL_EXPORTER`          | 可选                    | MySQL/MariaDB 指标，端口 9104                                    |
| `ENABLE_POSTGRES_EXPORTER`       | 可选                    | PostgreSQL 指标，端口 9187                                       |
| `ENABLE_REDIS_EXPORTER`          | 可选                    | Redis/Valkey 指标，端口 9121                                     |
| `*_IMAGE`                        | 启用对应 exporter       | 固定 Docker 镜像版本，可手工改成已审核版本或 digest              |
| `*_CONFIG` / `*_PASSWORD_FILE` | 启用对应数据库 exporter | 相对本目录或绝对路径的凭据文件                                   |
| `*_EXPORTER_NETWORK`             | 启用对应数据库 exporter | `host` 或数据库容器所在的已有 Docker 网络名                    |
| `START_EXPORTERS`                | 启用任一 exporter       | `false` 时只生成、安装和校验 Compose，不拉取镜像或启动容器     |

镜像、连接 URI、Docker 数据目录等详细字段已经写在 `deploy.conf.example` 的相应组件旁边。
配置文件只允许模板中的白名单字段，布尔值只允许 `true` 或 `false`，值两侧不要加引号。
部署脚本不会通过 `source` 把配置当成 root Shell 命令执行。

如果启用 Alloy，再编辑 `alloy/services.map`。每行代表一个服务实例：

```text
# service|environment|runtime_type|runtime_name|log_source|log_path
pixel-forge-backend|prod|systemd|pixel-forge-backend.service|journal|-
sub2api|prod|docker|sub2api|docker|-
vsftpd|prod|systemd|vsftpd.service|file|/var/log/vsftpd.log
```

支持的 `log_source` 是 `journal`、`docker`、`file`、`none`。完整字段规则和文件日志 ACL
见 [`alloy/README.md`](alloy/README.md)。

如果只需要服务发现，不需要日志采集，只需设置：

```ini
ENABLE_ALLOY=false
```

此时不需要创建 `alloy/services.map`，也不会安装 Alloy。

## 三、打包并上传

在 AIOMS 项目机器执行：

```bash
tar -czf /tmp/aioms-target-server.tar.gz -C scripts target-server
scp /tmp/aioms-target-server.tar.gz root@web01:/tmp/
```

也可以直接复制目录：

```bash
scp -r scripts/target-server root@web01:/tmp/aioms-target-server
```

`deploy.conf` 和 `services.map` 不应该包含密码或私钥；数据库密码放在独立凭据文件中。实际
环境配置和凭据文件已被本目录 `.gitignore` 排除，但打包时仍会进入压缩包，因此只应交给目标
服务器管理员，不要放到公开下载地址。

## 四、目标服务器一键部署

使用压缩包时：

```bash
ssh root@web01
cd /tmp
tar -xzf aioms-target-server.tar.gz
cd target-server
sudo ./deploy.sh
```

使用普通管理员账号上传时同样运行 `sudo ./deploy.sh`。也可指定放在其他位置的配置：

```bash
sudo ./deploy.sh --config /etc/aioms/web01.conf
```

总脚本执行顺序如下：

1. 校验部署配置。
2. 调用 `discovery/install.sh` 安装两个 root 所有的只读网关和 sudoers。
3. 若 `ENABLE_ALLOY=true`，调用 `alloy/install.sh` 安装软件、生成并校验配置。
4. 按五个 exporter 开关生成 `/etc/aioms-exporters/compose.yaml` 并安全复制凭据。
5. 若 `START_EXPORTERS=true`，拉取镜像并更新 `aioms-exporters` Compose 项目。

脚本可重复执行，用于更新同名网关、Alloy 和 exporter；Compose 使用 `--remove-orphans`，所以
重新部署时会停止并移除本项目中已被关闭的 exporter 容器，不会操作其他 Compose 项目。脚本
可能安装系统软件、写入 `/etc`、修改用户附加组、重启 Alloy 和更新监控容器，因此只应由
服务器管理员审核配置后执行。

如果五个 exporter 开关全部为 `false`，脚本会停止之前由本配置包部署的
`aioms-exporters` 项目，但保留 `/etc/aioms-exporters` 下的配置、凭据和备份；首次部署且没有
旧指标配置时不会要求服务器安装 Docker。

## 五、部署后验证

### 1. 验证 AIOMS 只读发现权限

以下示例假设 SSH 用户是 `aioms`：

```bash
sudo -l -U aioms
sudo visudo -cf /etc/sudoers.d/aioms-readonly
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-service-read inet-sockets
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-service-read unix-sockets
```

有 Docker 时再执行：

```bash
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-docker-read inventory
```

从 socket 输出中选择一个真实纯数字 PID，例如 `12345`，验证定点进程信息：

```bash
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-service-read process 12345
sudo -u aioms sudo -n -- /usr/local/sbin/aioms-service-read unit 12345
```

### 2. 验证 Alloy（仅启用时）

```bash
sudo ./alloy/check.sh
```

如果配置了 `START_ALLOY=false`，检查配置后由管理员启动：

```bash
sudo systemctl enable --now alloy
sudo ./alloy/check.sh
```

最后在中央 Loki 查询稳定标签，例如：

```logql
{server="web01", service="pixel-forge-backend", environment="prod"}
```

### 3. 验证指标 exporter（仅启用时）

```bash
sudo ./metrics/check.sh
```

然后把 [`metrics/prometheus-scrape.example.yml`](metrics/prometheus-scrape.example.yml) 中已启用
的 job 添加到中央 Prometheus，替换目标服务器 IP、服务名和环境，校验配置后 reload。中央查询：

```promql
up{server="web01"}
```

目标 exporter 是拉取模式：目标服务器部署成功并不等于中央已有指标，必须完成 Prometheus
抓取配置。详细数据库账号和端口说明见 [`metrics/README.md`](metrics/README.md)。

## 六、安全边界

sudoers 不直接开放任意 `docker`、`ss`、`ps`、`systemctl` 或 Shell，只允许执行：

```text
/usr/local/sbin/aioms-docker-read
/usr/local/sbin/aioms-service-read
```

网关内部只接受固定子命令，并校验容器名和纯数字 PID。安装后的网关必须保持 `root:root`
和 `0755`，sudoers 必须保持 `root:root` 和 `0440`；绝不能让 `aioms` 用户修改这些文件或
它们的上级目录。

启用 Docker 日志采集时，Alloy 需要访问 Docker socket。Docker 组通常具有接近 root 的控制
能力，因此必须通过 `ALLOW_DOCKER_SOCKET=true` 单独确认。只采集 journald 或文件日志时保持
为 `false`。

node_exporter 读取宿主机文件系统和 PID 信息；cAdvisor 按官方容器方式使用 `privileged`、
`/dev/kmsg`、Docker 数据目录等挂载。数据库 exporter 自身移除 Linux capabilities 并以只读
文件系统运行，但仍持有数据库监控凭据。Exporter HTTP 端点默认没有认证，只允许中央
Prometheus 通过内网或防火墙访问，不能暴露到公网。

AIOMS 的业务资产扫描仍遵循最小读取原则：先盘点 systemd 和 Docker，再扫描 TCP/UDP、Unix
Socket，只对监听结果中的纯数字 PID 定点读取进程详情，不读取整机 `ps aux`。

## 七、常见问题

### `用户 aioms 不存在`

先按服务器账号管理流程创建专用普通用户并配置 SSH 公钥，再重新部署。本脚本不会替你决定
账号、Shell、用户组或密钥策略。

### 映射包含 Docker 日志，但没有授权

确认确实需要容器 stdout/stderr，并了解 Docker socket 权限后，把
`ALLOW_DOCKER_SOCKET=true`。不需要容器日志时，把对应映射改为 `none` 或删除该行。

### Alloy 启动失败

```bash
sudo alloy validate /etc/alloy/config.alloy
sudo journalctl -u alloy -n 100 --no-pager
```

安装器会在覆盖已有配置前生成 `/etc/alloy/config.alloy.backup.<时间>`。更详细的日志源权限、
配置生成方式和排错命令见 [`alloy/README.md`](alloy/README.md)。

### Exporter 容器启动失败

```bash
sudo docker compose \
  --project-name aioms-exporters \
  -f /etc/aioms-exporters/compose.yaml ps
sudo docker compose \
  --project-name aioms-exporters \
  -f /etc/aioms-exporters/compose.yaml logs --tail 100
```

常见原因是端口已被已有 exporter 占用、数据库凭据不正确、Docker 数据目录不是
`/var/lib/docker`，或虚拟机没有 cAdvisor 所需的 `/dev/kmsg`。关闭不适用组件后可重新执行
`sudo ./deploy.sh`。

### 只想单独更新某一部分

仍可直接运行底层脚本：

```bash
sudo ./discovery/install.sh aioms
sudo ./alloy/install.sh --help
sudo ./metrics/install.sh --help
```

日常首次部署优先使用 `deploy.sh`，避免漏掉其中一部分配置。
