# systemd 服务控制写网关

本目录为非 root `aioms` 用户安装一个固定的 root 包装器，只接受：

```text
start SERVICE.service
stop SERVICE.service
restart SERVICE.service
```

包装器会同时检查动作、systemd 单元格式、root 持有且不可写的
`/etc/aioms/service-control.allowlist`，并在操作前后核对服务状态。sudoers 只授权这个包装器，
不会授权任意 `systemctl` 或 Shell。

白名单由目标服务器 `deploy.conf` 的 `SERVICE_CONTROL_ALLOWLIST` 配置，例如：

```ini
SERVICE_CONTROL_ALLOWLIST=bt.service,nginx.service
```

留空表示拒绝全部服务控制。修改白名单后需要由服务器管理员重新运行 `deploy.sh`。
