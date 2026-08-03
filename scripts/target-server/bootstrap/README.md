# AIOMS SSH 角色初始化

这个目录解决目标服务器首次接入 AIOMS 时的 SSH 身份和最小权限配置问题。流程分成两端：

1. AIOMS 客户端生成“只读”和“部署”两组 Ed25519 密钥。
2. 客户端生成一个只含两个公钥和角色脚本的小型角色包。
3. 管理员手动把角色包复制到目标服务器，以 root 执行一次角色配置脚本。

角色初始化不会安装服务发现网关、Alloy、exporter 或完整部署包，也不会启动服务。

通常直接使用上一层 `make-bundle.sh`：它会调用本目录脚本，并把角色初始化和完整基础部署组合
成目标服务器上的一次 `sudo ./install.sh`。本说明保留给只想单独配置 SSH 角色的场景。

## 1. 客户端生成密钥和角色包

在 AIOMS 项目根目录执行：

```bash
scripts/target-server/bootstrap/prepare-client.sh \
  --server web01 \
  --host 服务器地址 \
  --port 22
```

默认生成：

```text
ssh/web01-aioms                 # 日常只读私钥，不能上传
ssh/web01-aioms.pub
ssh/web01-aioms-deploy          # 受控部署私钥，不能上传
ssh/web01-aioms-deploy.pub

scripts/target-server/bootstrap/generated/web01/
├── readonly.pub                # 只读角色公钥
├── deploy.pub                  # 部署角色公钥
├── configure-roles.sh          # 管理员在目标服务器执行的自包含脚本
├── client-connection-info.txt  # 复制、安装、连接命令
└── SHA256SUMS                  # 上述四个文件的校验清单
```

私钥没有口令，以便 AIOMS 后端无人值守使用，因此必须只保存在后端机器。密钥目录权限应为
`0700`，私钥权限应为 `0600`。本脚本不会把私钥放入角色包，也不会修改本机
`~/.ssh/config` 或 `known_hosts`。

已有完整且匹配的密钥对会被复用；脚本不会覆盖已有私钥。再次运行只会刷新角色包中的公钥、
角色脚本、连接说明和校验清单。

## 2. 手动复制角色包

只复制生成的 `web01` 目录，不需要复制整个 `scripts/target-server/`：

```bash
scp -P 22 -r \
  scripts/target-server/bootstrap/generated/web01 \
  管理员账号@服务器地址:/tmp/aioms-role-web01
```

`prepare-client.sh` 会打印 `SHA256SUMS` 文件自身的摘要。建议通过与文件传输不同的可信渠道
把这个摘要告诉管理员；否则攻击者若能同时替换角色包和校验清单，包内自校验不能证明来源。

角色包不包含 `deploy.conf`、Alloy 映射、数据库凭据或任何私钥，可以和完整运行包分开审核、
分开传输。

## 3. 管理员在目标服务器初始化角色

管理员先比较 `SHA256SUMS` 文件摘要，再执行：

```bash
cd /tmp/aioms-role-web01
sha256sum -c SHA256SUMS
sudo ./configure-roles.sh \
  --readonly-user aioms \
  --deploy-user aioms-deploy
```

脚本自身还会再次校验 `SHA256SUMS`，然后创建或复用两个普通 Linux 用户，并生成：

```text
/home/aioms/.ssh/authorized_keys
/home/aioms-deploy/.ssh/authorized_keys
/usr/local/sbin/aioms-target-deploy
/etc/sudoers.d/aioms-deploy
/opt/aioms-target-server/             # 仅预留 root 所有的固定部署目录
/var/lib/aioms-deploy/incoming/        # 部署用户唯一可写的配置暂存目录
```

两个公钥都会带上禁止 SSH agent 转发、端口转发、X11 转发和 PTY 的限制。已有
`authorized_keys` 会保留；同一把公钥如果已经存在，会被替换为当前受限版本。

部署用户新增的 sudo 权限只有两个固定子命令：

```text
sudo /usr/local/sbin/aioms-target-deploy apply
sudo /usr/local/sbin/aioms-target-deploy apply-alloy
```

完整包部署子命令 `apply` 只会读取以下两个固定路径：

```text
/opt/aioms-target-server/deploy.sh
/opt/aioms-target-server/deploy.conf
```

在管理员后续审核并以 `root:root` 安装这两个文件前，入口会明确拒绝执行。部署用户不能选择
其他脚本、其他配置，也不能写入 `/opt/aioms-target-server/`。

`apply-alloy` 只会调用后续基础部署安装的 root 所有 Alloy 应用器。AI 可以把自动生成的服务映射
上传到固定 incoming 目录，但不能指定 root 脚本、目标路径、Loki 地址或任意文件日志路径。

## 4. 为什么此时没有 aioms-readonly sudoers

角色初始化只安装 SSH 身份，不提前生成 `/etc/sudoers.d/aioms-readonly`。该 sudoers 会授权：

```text
/usr/local/sbin/aioms-service-read
/usr/local/sbin/aioms-docker-read
```

这两个只读网关此时还没有安装。后续管理员部署完整 `target-server` 包时，
`discovery/install.sh` 会先安装 root 所有的网关，再校验并生成对应 sudoers，避免权限文件指向
不存在或未经审核的程序。

## 5. 客户端验证

先从可信控制台或管理员处核对目标服务器的 SSH host key 指纹，再把它记录到运行 AIOMS 后端
机器的 `known_hosts`。随后可以验证两个账号：

```bash
ssh -p 22 -i ssh/web01-aioms aioms@服务器地址 'id'
ssh -p 22 -i ssh/web01-aioms-deploy aioms-deploy@服务器地址 'id'
```

AIOMS `servers` 表中的日常连接使用只读身份：

```text
ssh_user=aioms
ssh_key_filename=web01-aioms
deploy_ssh_user=aioms-deploy
deploy_ssh_key_filename=web01-aioms-deploy
```

部署密钥 `web01-aioms-deploy` 不应作为日常查询密钥；只有经过人工审批的服务器写操作才能
选择它。SSH 角色和服务器内最小 sudo 权限只负责最后一道执行边界，不代替 AIOMS 应用层审批。
