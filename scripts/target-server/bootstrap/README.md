# AIOMS 单一 SSH 角色初始化

这个目录用于首次创建一个非 root 的 `aioms` SSH 身份。该身份同时承担只读查询和审批后的
白名单写操作，但 Linux 权限仍由 root 持有的固定包装器隔离，不会开放任意 `systemctl`、
`bash`、`sh`、`docker` 或 root 登录。

角色初始化不会安装服务发现网关、Alloy、exporter 或完整部署包，也不会启动服务。通常直接
使用上一层 `make-bundle.sh`；下面的流程只适合单独初始化 SSH 身份。

## 1. 客户端生成密钥和角色包

在 AIOMS 项目根目录执行：

```bash
scripts/target-server/bootstrap/prepare-client.sh \
  --server web01 \
  --host 服务器地址 \
  --port 22 \
  --user aioms
```

默认生成：

```text
ssh/web01-aioms                 # 私钥，不能上传
ssh/web01-aioms.pub

scripts/target-server/bootstrap/generated/web01/
├── aioms.pub                   # 角色包只含公钥
├── configure-role.sh           # 管理员在目标服务器执行
├── client-connection-info.txt
└── SHA256SUMS
```

私钥没有口令，以便 AIOMS 后端无人值守使用，因此只能保存在后端机器。密钥目录应为 `0700`，
私钥应为 `0600`。脚本不会把私钥放入角色包，也不会修改本机 SSH 配置；已有完整且匹配的密钥
会复用，不会覆盖。

## 2. 管理员初始化角色

通过受控渠道复制生成的 `web01` 目录，在目标服务器核对校验清单后执行：

```bash
cd /tmp/aioms-role-web01
sha256sum -c SHA256SUMS
sudo ./configure-role.sh --user aioms
```

脚本创建或复用一个普通用户，并生成：

```text
/home/aioms/.ssh/authorized_keys
/usr/local/sbin/aioms-target-deploy
/etc/sudoers.d/aioms-target-deploy
/opt/aioms-target-server/
/var/lib/aioms/incoming/
```

公钥带有禁止 SSH agent 转发、端口转发、X11 转发和 PTY 的限制。新增的 sudo 权限只有：

```text
sudo /usr/local/sbin/aioms-target-deploy apply
sudo /usr/local/sbin/aioms-target-deploy apply-alloy
```

`apply` 只能读取 `/opt/aioms-target-server/` 中 root 持有且不可被 `aioms` 修改的固定脚本与
配置；`apply-alloy` 只能调用 root 持有的固定应用器。后续 `discovery/install.sh` 和
`actions/install.sh` 会在对应包装器安装完成后，再为同一用户增加精确 sudoers 权限。

## 3. 客户端配置与验证

AIOMS `servers` 表只配置一套连接字段：

```text
ssh_user=aioms
ssh_key_filename=web01-aioms
```

先从可信控制台核对目标服务器 host key，再验证普通身份：

```bash
ssh -p 22 -i ssh/web01-aioms aioms@服务器地址 'id'
```

同一 SSH 身份降低了学习项目的配置复杂度，但不会降低网关边界：审批属于 AIOMS 应用层，
目标服务器 sudoers 和 root 所有包装器负责最终阻止任意命令执行。

如果目标服务器曾配置旧的 `aioms-deploy` 双角色，确认新单角色安装和读写网关都可用后，再由
管理员人工移除旧用户的 authorized key 与旧 `/etc/sudoers.d/aioms-deploy`；脚本不会自动删除
已有账号或权限，避免误删管理员自定义配置。
