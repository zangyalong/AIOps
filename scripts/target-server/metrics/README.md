# 目标服务器 Prometheus exporter 配置

本目录由根目录 `deploy.sh` 调用，使用一个名为 `aioms-exporters` 的 Docker Compose 项目管理：

| 组件 | 默认端口 | 数据范围 | 权限特点 |
| --- | ---: | --- | --- |
| node_exporter | 9100 | 宿主机 CPU、内存、磁盘、网络、进程数 | 只读挂载宿主机根目录，使用 host PID/network |
| cAdvisor | 8080 | Docker 容器 CPU、内存、网络、OOM 等 | 按官方容器方式使用 privileged 和宿主机目录挂载 |
| mysqld_exporter | 9104 | MySQL/MariaDB 状态 | 使用手工提供的 `.my.cnf` |
| postgres_exporter | 9187 | PostgreSQL 连接、锁、事务、复制等 | 使用 URI、用户名和独立密码文件 |
| redis_exporter | 9121 | Redis/Valkey 内存、连接、keyspace 等 | 使用地址、可选 ACL 用户和独立密码文件 |

五个组件均由 `deploy.conf` 的独立开关控制。目标服务器必须已有 Docker 和 Docker Compose；
安装器不会自动安装 Docker，也不会自动创建数据库账号。

模板中的镜像使用截至编写时已发布的固定版本，不使用 `latest`。版本来源分别是
[node_exporter](https://github.com/prometheus/node_exporter/releases)、
[cAdvisor](https://github.com/google/cadvisor/releases)、
[mysqld_exporter](https://github.com/prometheus/mysqld_exporter/releases)、
[postgres_exporter](https://github.com/prometheus-community/postgres_exporter/releases) 和
[redis_exporter](https://github.com/oliver006/redis_exporter/releases)。升级时先修改 `deploy.conf`
中的镜像，再重新执行部署。

关闭某一个开关并重新部署时，Compose 会移除该项目中对应的旧 exporter。五个开关全部关闭
时，`deploy.sh` 会停止整个 `aioms-exporters` 项目，但保留 `/etc/aioms-exporters` 中的配置、
凭据和备份，便于以后重新启用。

## 凭据文件

启用数据库 exporter 前复制需要的示例：

```bash
cp metrics/mysql-exporter.my.cnf.example metrics/mysql-exporter.my.cnf
cp metrics/postgres-exporter.password.example metrics/postgres-exporter.password
cp metrics/redis-exporter.password.example metrics/redis-exporter.password
```

只复制已启用数据库需要的文件。实际文件已被 `.gitignore` 排除，部署时会复制到
`/etc/aioms-exporters/secrets/`，保存为 `root:aioms-exporters`、权限 `0640`。密码不会写入
生成的 Compose；但拥有 Docker 管理权限的人仍等同于拥有主机高权限。

三个数据库 exporter 都有 `*_EXPORTER_NETWORK` 配置。数据库运行在宿主机并已监听本机 TCP
端口时填写 `host`，连接地址使用 `127.0.0.1`；数据库运行在未发布端口的 Docker 容器中时，
填写它所在的已有 Docker 网络名，连接地址改为容器名，例如：

```ini
POSTGRES_EXPORTER_NETWORK=sub2api_default
POSTGRES_EXPORTER_URI=sub2api-postgres:5432/postgres?sslmode=disable
REDIS_EXPORTER_NETWORK=sub2api_default
REDIS_EXPORTER_ADDRESS=redis://sub2api-redis:6379
```

部署脚本只接入已存在的网络，不创建或修改业务 Docker 网络。

## 数据库账号由管理员创建

MySQL/MariaDB 示例权限：

```sql
CREATE USER 'aioms_exporter'@'localhost'
  IDENTIFIED BY '替换成强密码'
  WITH MAX_USER_CONNECTIONS 3;
GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'aioms_exporter'@'localhost';
```

如果 exporter 通过 TCP 连接时数据库看到的来源不是 `localhost`，请按实际网络设置账号 host，
不要直接使用 `'%'` 放开所有来源。

PostgreSQL 10+ 示例权限：

```sql
CREATE USER aioms_exporter WITH PASSWORD '替换成强密码';
GRANT CONNECT ON DATABASE postgres TO aioms_exporter;
GRANT pg_monitor TO aioms_exporter;
```

Redis 6+ 建议创建专用 ACL 用户，只开放 exporter 读取 INFO、CONFIG GET、CLIENT、SLOWLOG 等
监控命令。不同 Redis/Valkey 版本的命令集合有差异，应按当前 exporter 官方说明配置，不要复用
管理员账号。

## 中央 Prometheus 仍需增加抓取目标

Exporter 采用 Prometheus 拉取模式，目标服务器不会主动把指标推给中央 Prometheus。复制
`prometheus-scrape.example.yml` 中已启用的 job 到中央 `scrape_configs`，把示例 IP、服务器名、
环境和服务名改成服务目录中的实际值，然后校验并 reload Prometheus。

cAdvisor 模板会优先把容器的 `aioms.service` Docker label 映射为统一 `service` 标签；没有该
label 时依次退化为 Docker Compose service 和容器名。重要业务容器建议显式增加
`aioms.service=<服务目录名称>` 标签。

只允许中央 Prometheus 访问以下已启用端口，不要直接暴露到公网：

```text
9100  node_exporter
8080  cAdvisor
9104  mysqld_exporter
9187  postgres_exporter
9121  redis_exporter
```

本包不会修改防火墙，因为不同服务器使用 firewalld、nftables、安全组或云防火墙，自动开放端口
可能扩大暴露面。

## 检查

```bash
sudo ./metrics/check.sh
```

它会检查生成的 Compose、容器状态和本机各 `/metrics` 端点，不会修改运行状态。中央侧再查询：

```promql
up{server="web01"}
```
