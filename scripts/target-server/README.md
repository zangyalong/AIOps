# AIOMS 目标服务器配置指南

推荐在 AIOMS 前端的服务器编辑页填写“环境部署”并下载单服务器安装包，目标服务器解压后只
执行一次 `sudo ./install.sh`。命令行 `make-bundle.sh` 仍保留，适合离线或排错。
也可以把整个 `scripts/target-server/` 目录复制到目标 Linux 服务器，按底层步骤分别执行。
首次安装可以完成：

1. 安装无网络、宿主机只读并遮蔽敏感路径的 root 诊断沙箱。
2. 安装 AIOMS 服务发现所需的结构化只读网关和精确 sudoers 权限。
3. 为同一个非 root `aioms` 用户安装统一的 systemd / Docker 白名单服务控制写网关；白名单留空时拒绝全部。
4. 按需安装 Grafana Alloy 基础组件；服务发现并注册后由 AI 生成日志配置并审批启用。
5. 按独立开关部署 node_exporter、cAdvisor、MySQL、PostgreSQL 和 Redis exporter。

如果目标服务器还没有 AIOMS 专用 SSH 用户，先使用 `bootstrap/prepare-client.sh` 在客户端
生成一组密钥和一个独立角色包。服务器管理员只需接收一个公钥和角色配置脚本，不需要接收
完整运行包，也不需要把 root 密码或私钥交给 AIOMS。

## 先分清目标服务器和中央监控服务器

| 位置                      | 本配置包负责                                                | 本配置包不负责                                                                |
| ------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------- |
| 目标服务器（如`web01`） | 首次 bootstrap 可创建一个非 root SSH 用户并安装公钥；日常包负责固定读写网关、可选 Alloy 和 exporter | 不接收管理员私钥，不自动创建数据库监控账号，不修改防火墙 |
| 中央监控服务器            | 无                                                          | Loki、Prometheus、Alertmanager、Grafana 仍按中央监控栈单独部署                |

Alloy 和全部 exporter 都是可选组件。只做服务发现时关闭所有采集开关；需要日志时启用
Alloy，需要哪类指标就单独启用对应 exporter。

## 推荐：前端生成一个安装包、服务器执行一条命令

在 AIOMS 前端打开“服务器 → 编辑”，依次填写：

1. AIOMS SSH 身份：端口和非 root 用户名（例如 `22`、`aioms`）。
2. 环境部署：环境名、Loki 地址，以及 Alloy、node_exporter、cAdvisor 和数据库 exporter 开关。
3. 启用数据库 exporter 时，填写连接参数和本次部署密码。

首次点击“保存并下载首次安装包”。后端会生成或复用一组 Ed25519 密钥，私钥只保留在后端的
`SSH_KEY_DIR`，浏览器下载 `aioms-<server>.tar.gz`。非敏感环境配置会随服务器记录保存；
数据库密码不会写入数据库或返回服务器详情，只会进入当次压缩包或自动部署请求的独立凭据
文件，请求完成后服务端临时数据会被清理。

首次安装完成后，修改环境部署配置可直接点击“保存并自动部署”，也可以在 AI 对话中要求部署
该服务器。按钮可提交本次数据库密码；高风险 Tool 经过人工审批后只复用目标服务器已有凭据，
不接收密码。配置更新不再要求重新下载压缩包；只有固定部署程序自身升级时才需要管理员重新
执行新的首次安装包。

首次部署仍不填写服务器上的服务清单。安装完成后由 AI 对话运行发现 Tool，注册服务，再生成
并审批应用 Alloy 服务映射，所以服务发现能力不会被前置手工配置替代。

如果不使用前端，也可以在 AIOMS 后端机器命令行生成：

首次只填写服务器级配置，不填写服务列表：

```bash
cd scripts/target-server
cp deploy.conf.example deploy.conf
# 编辑 deploy.conf：服务器名、Loki 地址、组件开关和可选数据库凭据

./make-bundle.sh \
  --server web01 \
  --host 服务器地址
```

本地生成：

```text
ssh/web01-aioms                         # 私钥，只留在 AIOMS 后端机器
scripts/target-server/dist/aioms-web01.tar.gz
```

把压缩包复制到目标服务器后，管理员只执行：

```bash
tar -xzf aioms-web01.tar.gz
cd aioms-web01
sudo ./install.sh
```

这一条命令内部依次配置单一 SSH 身份、安装 root 只读沙箱、discovery 和固定写网关，再按开关安装基础指标及未配置服务映射的
Alloy。完成后即可回到 AIOMS 对话中调查、发现、注册服务，再预览并审批应用日志配置。压缩包不含
私钥；如果启用了数据库 exporter，会包含配置引用的监控凭据，因此仍要通过受控渠道传输。

## 目录说明

```text
target-server/
├── make-bundle.sh               # 本地生成单服务器首次安装包
├── deploy.sh                    # 一键部署总入口
├── deploy.conf.example          # 每台服务器的部署参数模板
├── bootstrap/
│   ├── prepare-client.sh        # 客户端生成一组密钥和单一角色包
│   ├── server-bootstrap.sh      # 角色包中 configure-role.sh 的源文件
│   └── README.md                # 首次引导的完整操作流程
├── root-read/
│   ├── Dockerfile               # 固定诊断工具镜像
│   ├── entrypoint.py            # stdin JSON 校验、超时、截断和脱敏
│   ├── host_mounts.py           # 安全枚举宿主机独立挂载点
│   ├── aioms-root-read          # root 持有的一次性沙箱启动器
│   ├── aioms-root-read.sudoers  # 只开放无参数固定入口
│   ├── install.sh               # 构建镜像并固定不可变 image ID
│   └── README.md                # 沙箱边界与验收命令
├── discovery/
│   ├── install.sh               # 只读发现网关安装器，由 deploy.sh 调用
│   ├── aioms-service-read       # socket、指定 PID 和 systemd unit 只读查询网关
│   ├── aioms-docker-read        # Docker 只读查询网关
│   ├── aioms-readonly.sudoers   # 最小 sudo 权限模板
│   └── README.md                # 发现命令与权限边界
├── actions/
│   ├── install.sh               # systemd / Docker 统一服务控制写网关安装器
│   ├── aioms-service-control    # 只接受 start/stop/restart + 白名单运行时身份
│   ├── aioms-service-policy     # 只接受页面授权触发的白名单 add/remove
│   ├── aioms-service-control.sudoers
│   └── README.md                # 双端白名单和权限边界
├── files/
│   ├── install.sh               # 文件查找、读取、授权和写入网关安装器
│   ├── aioms-file-read          # 授权范围内查找并脱敏读取文本
│   ├── aioms-file-policy        # 页面触发的 read/write 白名单同步
│   ├── aioms-file-write         # 整文件/字段补丁原子替换与 Nginx 事务发布
│   ├── config_patch.py          # dotenv / JSON 非敏感标量补丁器
│   └── README.md                # 敏感路径、大小和审批边界
├── alloy/
│   ├── install.sh               # Alloy 基础组件/管理员配置安装器
│   ├── aioms-alloy-apply        # root 所有的固定 AI 配置应用器
│   ├── generate-config.sh       # 根据服务映射生成 Alloy 配置
│   ├── check.sh                 # Alloy 安装后检查
│   ├── services.map.example     # 管理员手工配置时的映射模板
│   └── README.md                # 自动生成流程、权限和排错细节
└── metrics/
    ├── install.sh               # 可选 exporter 的 Docker Compose 安装器
    ├── check.sh                 # 容器和 /metrics 端点检查
    ├── *password.example        # 数据库密码文件模板
    ├── mysql-exporter.my.cnf.example
    ├── prometheus-scrape.example.yml
    └── README.md                # 数据库账号、权限、端口和中央抓取说明
```

普通配置、代码和日志调查由 `root-read/` 提供，不需要维护读取命令或业务目录白名单。运行包仍会安装 `files/` 下的受控文件网关；它用于“准备修改文件”的快照、哈希复核和审批写入。文件写白名单默认为空，需要在 AIOMS 服务目录的实例详情中登记“精确可写文件”；页面会通过同一个非 root SSH 身份把授权同步到目标机 root 持有的 `/etc/aioms/file-access.allowlist`。

受控文件快照仅支持不超过 64 KiB 的文本文件并自动脱敏。普通整文件写入不会接收密码、Token、私钥或脱敏占位符，执行前必须展示完整 diff 并通过高风险二次确认。结构化补丁可在不读取其他真实字段的情况下修改 dotenv 已有变量或 JSON Pointer 指向的已有非敏感标量，使用中风险单次审批；YAML 尚未启用。Nginx 专用发布在完整 diff 审批后自动执行配置测试和 reload，失败恢复备份。目标机备份统一位于 `/var/lib/aioms/file-backups/`。

长期生效的文件会安装到 `/usr/local/sbin/`、`/etc/sudoers.d/`、`/etc/alloy/` 和
`/etc/aioms-exporters/`。上传到临时目录的配置包在验证完成后可以删除。

## 一、部署前准备

目标服务器需要满足：

- Linux 使用 systemd，并有 Bash、sudo、`visudo` 和 `ss`（iproute2）。
- AIOMS 用于 SSH 登录的普通用户已经存在并安装公钥，例如 `aioms`；没有时先执行下一节的
  首次 SSH 初始化。
- 启用 Alloy 时，目标服务器能访问中央 Loki 的 HTTP 推送地址。
- Docker Engine 25+ 是 root 只读沙箱禁用递归并逐项只读挂载的必需依赖；Linux 5.10 可用。启用任一指标 exporter 时还必须已有 Docker Compose。

日常 `deploy.sh` 不会创建 SSH 用户或写入公钥；只有管理员主动执行首次 bootstrap 才会做这两
件事。两个流程都不会修改 `sshd_config`，服务器包永远不包含客户端私钥。

## 零、首次 SSH 初始化（每台服务器只做一次）

在 AIOMS 项目根目录生成一组共用的 SSH 密钥：

```bash
scripts/target-server/bootstrap/prepare-client.sh \
  --server web01 \
  --host 服务器地址 \
  --port 22
```

私钥保存在项目根目录 `ssh/`，不会进入角色包。可单独复制的角色包生成在
`scripts/target-server/bootstrap/generated/web01/`，内容只有一个公钥、`configure-role.sh`、
连接说明和 SHA-256 清单。把这个 `web01` 目录复制给服务器管理员即可，不需要同时打包完整
`target-server` 目录或部署凭据。

管理员在目标服务器核对脚本和 SHA-256 后执行：

```bash
cd /tmp/aioms-role-web01
sha256sum -c SHA256SUMS
sudo ./configure-role.sh --user aioms
```

bootstrap 创建或复用一个 `aioms` 普通用户。它既用于只读查询，也用于审批后调用固定写网关。
该用户可以把通用调查命令送入 root 只读沙箱，但不能在宿主机执行任意 root Shell，也不能指定
额外脚本、挂载、镜像、网络、`systemctl` 或 Docker 控制命令。

角色脚本还会生成 `/usr/local/sbin/aioms-target-deploy` 和
`/etc/sudoers.d/aioms-target-deploy`，并预留 root 所有的 `/opt/aioms-target-server/`。它不会复制运行
包、安装发现网关、启动 Alloy 或 exporter。完整文件位置、验证命令和密钥边界见
[`bootstrap/README.md`](bootstrap/README.md)。

## 二、在打包前填写配置

在项目机器的 `scripts/target-server/` 中执行：

```bash
cp deploy.conf.example deploy.conf
```

首次安装不需要填写服务清单。数据库 exporter 的凭据文件只复制已启用的组件：

```bash
cp metrics/mysql-exporter.my.cnf.example metrics/mysql-exporter.my.cnf
cp metrics/postgres-exporter.password.example metrics/postgres-exporter.password
cp metrics/redis-exporter.password.example metrics/redis-exporter.password
```

编辑 `deploy.conf`：

```ini
AIOMS_SSH_USER=aioms
SERVICE_CONTROL_ALLOWLIST=systemd:bt.service,systemd:nginx.service,docker:orders-api
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
| `AIOMS_SSH_USER`                 | 始终必填                | 非 root AIOMS SSH 用户，应与 `servers.ssh_user` 一致；读写能力都只能经过固定网关 |
| `SERVICE_CONTROL_ALLOWLIST`      | 使用服务控制 Tool       | 允许启停/重启的 `systemd:unit.service` 或 `docker:container`；留空拒绝全部 |
| `ENABLE_ALLOY`                   | 始终必填                | `true` 安装 Alloy 基础组件，`false` 只安装发现和指标组件     |
| `SERVER_NAME`                    | 启用 Alloy              | 稳定服务器标签，如`web01`                                      |
| `LOKI_PUSH_URL`                  | 启用 Alloy              | 从目标服务器可访问的中央 Loki 地址                               |
| `ALLOY_APT_REPO_URL` / `ALLOY_APT_GPG_KEY_URL` | 可选（Debian/Ubuntu） | APT 仓库和签名公钥；默认 Grafana 官方地址，内网镜像须成对替换 |
| `ALLOY_RPM_REPO_URL` / `ALLOY_RPM_GPG_KEY_URL` | 可选（RHEL/Fedora） | 默认临时使用清华 TUNA 仓库，RPM 包仍用 Grafana 官方公钥校验 |
| `ALLOY_PACKAGE_PROXY_URL`        | 可选                    | 仅本次 Alloy 下载使用的 HTTP(S) 代理，不写入系统全局代理        |
| `ALLOY_SERVICE_MAP`              | 可选                    | 首次留空；管理员已有审核映射时可填写相对或绝对路径               |
| `ALLOW_DOCKER_SOCKET`            | 需要 Docker 日志时      | root 侧权限策略；AI 不能自行从 `false` 改为 `true`            |
| `START_ALLOY`                    | 有手工映射时            | 首次无映射必须为 `false`；AI 审批应用后会启动或重载            |
| `ENVIRONMENT`                    | 启用任一 exporter       | Prometheus 统一环境标签，如`prod`                              |
| `ENABLE_NODE_EXPORTER`           | 可选                    | 宿主机 CPU、内存、磁盘和网络，端口 9100                          |
| `ENABLE_CADVISOR`                | 可选                    | Docker 容器资源指标，端口 8080                                   |
| `ENABLE_MYSQL_EXPORTER`          | 可选                    | MySQL/MariaDB 指标，端口 9104                                    |
| `ENABLE_POSTGRES_EXPORTER`       | 可选                    | PostgreSQL 指标，端口 9187                                       |
| `ENABLE_REDIS_EXPORTER`          | 可选                    | Redis/Valkey 指标，端口 9121                                     |
| `*_IMAGE`                        | 启用对应 exporter       | 固定 Docker 镜像版本，可手工改成已审核版本或 digest              |
| `EXPORTER_IMAGE_MIRROR_PREFIX`   | 可选                    | 默认 `m.daocloud.io`，只用于本次拉取，不修改 Docker daemon       |
| `*_CONFIG` / `*_PASSWORD_FILE` | 启用对应数据库 exporter | 相对本目录或绝对路径的凭据文件                                   |
| `*_EXPORTER_NETWORK`             | 启用对应数据库 exporter | `host` 或数据库容器所在的已有 Docker 网络名                    |
| `START_EXPORTERS`                | 启用任一 exporter       | `false` 时只生成、安装和校验 Compose，不拉取镜像或启动容器     |

镜像、连接 URI、Docker 数据目录等详细字段已经写在 `deploy.conf.example` 的相应组件旁边。
配置文件只允许模板中的白名单字段，布尔值只允许 `true` 或 `false`，值两侧不要加引号。
部署脚本不会通过 `source` 把配置当成 root Shell 命令执行。

Exporter 默认通过 DaoCloud 前缀代理临时拉取 Quay、GHCR 和 Docker Hub 镜像，然后标记回原始
镜像名并删除代理别名。这个过程不会修改 `/etc/docker/daemon.json` 或重启 Docker；如果已配置
企业内部镜像仓库，可以直接把各 `*_IMAGE` 改成内部完整镜像名，并将
`EXPORTER_IMAGE_MIRROR_PREFIX=` 留空。

### Alloy 内网源和下载代理

APT 默认使用 Grafana 官方仓库；DNF 默认只在 Alloy 安装期间使用清华 TUNA 镜像，完成或失败后
都会恢复服务器原有的 `/etc/yum.repos.d/grafana.repo`。TUNA 不提供仓库元数据签名，因此安装器
对该临时镜像使用 `repo_gpgcheck=0`，但始终保留 `gpgcheck=1`，继续通过 Grafana 官方 GPG key
校验实际下载的 RPM 包。

网络出口较慢时，也可以在 `deploy.conf` 填入公司内部、已经审核过的镜像地址；仓库和它对应的
GPG 公钥地址必须一起替换。例如 Debian/Ubuntu：

```ini
ALLOY_APT_REPO_URL=https://packages.example.internal/grafana/apt
ALLOY_APT_GPG_KEY_URL=https://packages.example.internal/grafana/gpg-full.key
```

RHEL/Fedora 使用对应的 `ALLOY_RPM_REPO_URL` 和 `ALLOY_RPM_GPG_KEY_URL`。镜像若使用内部 CA，
先把该 CA 安装到目标服务器信任库；部署脚本不会为了下载软件而关闭 TLS 校验。

如果仅需经代理访问官方源，填写：

```ini
ALLOY_PACKAGE_PROXY_URL=http://proxy.example.internal:7890
```

代理只传给本次 `wget`、`apt-get` 或 `dnf` 下载命令，脚本不会修改 `/etc/apt/apt.conf.d`、DNF 配置
或系统环境变量。代理 URL 包含账号密码时，`deploy.conf` 和安装包包含敏感信息，务必限制权限和传输范围。

首次不要编辑 `alloy/services.map`。基础部署完成后，直接在 AIOMS 对话中说：

```text
发现 web01 上的服务，并注册到服务目录。
```

批准注册后再说：

```text
预览并应用 web01 的日志采集配置。
```

AI 会从服务目录生成映射：systemd 使用 journald、Docker 使用容器 stdout/stderr、未纳管进程
标记为 `none`。预览不修改服务器；应用会显示第二张高风险审批卡片，批准后才上传固定映射、
校验并启动或重载 Alloy。文件日志路径无法从进程可靠推断，仍由管理员人工配置。完整规则见
[`alloy/README.md`](alloy/README.md)。

如果只需要服务发现，不需要日志采集，只需设置：

```ini
ENABLE_ALLOY=false
```

此时不会安装 Alloy，但 discovery 和已启用的指标组件仍会安装。

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
3. 若 `ENABLE_ALLOY=true` 且没有映射，安装 Alloy 与固定应用器但保持未启动；有管理员映射时
   才直接生成配置。
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

### 2. 验证 Alloy（应用服务映射后）

```bash
sudo ./alloy/check.sh
```

首次无服务映射时 Alloy 处于未启动状态，这是预期结果。先通过对话完成服务发现、注册、配置
预览和审批应用，再运行检查。管理员手工安装映射时也可以启动：

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

同一个 `aioms` 用户还可调用三个精确部署入口：

```text
/usr/local/sbin/aioms-target-deploy apply
/usr/local/sbin/aioms-target-deploy apply-config
/usr/local/sbin/aioms-target-deploy apply-alloy
```

`apply` 只能调用 `/opt/aioms-target-server/deploy.sh`，并且只能读取同目录下由 root 持有且
group/other 不可写的 `deploy.conf`。固定部署目录本身也必须由 root 持有且 group/other 不可
写；在管理员尚未安装这两个文件时，入口会拒绝执行。AIOMS 账号不能把上传目录中的脚本交给
root 执行。

`apply-config` 不接受路径或 Shell 参数，只读取 `/var/lib/aioms/incoming/` 下固定命名的配置、
部署清单和可选数据库凭据。root 固定应用器会校验上传用户、权限、文件类型、大小和清单字段，
部署失败时恢复并重新应用上一版配置。自动部署只能更新这些配置数据，不能上传或替换 root
执行脚本。

`apply-alloy` 不接受路径或配置参数，只调用 root 所有的固定应用器。部署 Tool 只能把自动生成的
`services.map` 上传到 `/var/lib/aioms/incoming/`；应用器重新校验文件所有者、权限、大小
和映射格式，从 `/etc/aioms/alloy-target.conf` 读取管理员确定的服务器名、Loki 地址和 Docker
socket 策略。自动流程拒绝 `file` 日志源，不能借 Alloy 读取任意文件。

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

先按“零、首次 SSH 初始化”生成角色包，由服务器管理员执行 `configure-role.sh`，再重新部署
完整运行包。日常 `deploy.sh` 不负责创建账号或写入 SSH 公钥。

### 映射包含 Docker 日志，但没有授权

确认确实需要容器 stdout/stderr，并了解 Docker socket 权限后，把
`deploy.conf` 中的 `ALLOW_DOCKER_SOCKET=true`，由管理员重新执行基础部署更新 root 侧策略，
然后重新发起 AI 配置审批。不需要容器日志时保持为 `false`；自动预览会列出 Docker 来源，
但目标网关会拒绝实际应用。

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
