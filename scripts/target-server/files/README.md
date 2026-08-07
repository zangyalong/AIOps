# 纳管服务器文件网关

文件能力使用同一个非 root `aioms` SSH 身份，但只能经 root 持有的三个固定包装器执行：

- `aioms-file-read`：在已授权目录查找文件、脱敏读取不超过 64 KiB 的文本文件；
- `aioms-file-policy`：由 AIOMS 服务目录页面同步可读目录或精确可写文件；
- `aioms-file-write`：校验预览时的 SHA-256、保留权限、生成备份并原子替换。

默认白名单为空。私钥、`/etc/shadow`、sudoers 等路径始终禁止；包含密码、Token、连接串凭据的行只返回脱敏值。
