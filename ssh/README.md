# ssh/

存放 AIOMS 用来 SSH 连接测试服务器的私钥（公钥也可以放这，方便一起管理）。这个目录除了本文件之外全部被 `.gitignore` 忽略，**不会提交任何密钥内容到仓库**。

## 约定

- `servers` 表里的 `ssh_key_filename` 字段存的是**文件名**（比如 `aioms-test`），不是完整路径；实际路径由后端在运行时拼成 `settings.ssh_key_dir / ssh_key_filename`（`app/core/config.py` 里定义），也就是这个目录下的同名文件。
- 私钥文件权限必须是 `600`，否则 SSH 客户端会拒绝使用（`ssh-keygen` 生成的默认就是对的，如果是别的方式拿到的私钥记得 `chmod 600`）。

## 生成一对新密钥

```bash
ssh-keygen -t ed25519 -f ssh/aioms-test -N "" -C "aioms-test-sandbox"
```

生成后会有 `ssh/aioms-test`（私钥）和 `ssh/aioms-test.pub`（公钥）两个文件。具体怎么用来起测试容器，见 [docs/local-ssh-sandbox.md](../docs/local-ssh-sandbox.md)。
