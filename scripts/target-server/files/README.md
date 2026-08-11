# 纳管服务器文件网关

文件能力使用同一个非 root `aioms` SSH 身份，但只能经 root 持有的三个固定包装器执行：

- `aioms-file-read`：在已授权目录查找文件、脱敏读取文本，并在目标机生成结构化补丁预览；
- `aioms-file-policy`：由 AIOMS 服务目录页面同步可读目录或精确可写文件；
- `aioms-file-write`：校验预览时的 SHA-256、保留权限、生成备份并原子替换；Nginx 发布还会校验、reload 和失败回滚；
- `config_patch.py`：只在目标机处理 dotenv 已有变量或 JSON Pointer 指向的已有标量，其他真实字段不会返回后端。

默认白名单为空。私钥、`/etc/shadow`、sudoers 等路径始终禁止；包含密码、Token、连接串凭据的行只返回脱敏值。

结构化补丁只允许精确写授权文件，并拒绝敏感字段、自动新增、对象/数组替换、重复 JSON 键和含换行值。dotenv 与 JSON 补丁只替换目标标量的原始字节；YAML 尚未开放，不能用正则模拟解析。普通文本仍走完整 diff 的整文件更新。

Nginx 专用发布仅接受 `/etc/nginx/` 或 `/www/server/panel/vhost/nginx/` 下已精确授权的 `.conf`。网关原子替换后调用固定位置的 Nginx 二进制执行 `-t` 和 `-s reload`；任一步失败都会恢复旧文件，reload 失败时还会尽力重新加载旧配置。
