# systemd 服务控制写网关

本目录为非 root `aioms` 用户安装一个固定的 root 包装器，只接受：

```text
start SERVICE.service
stop SERVICE.service
restart SERVICE.service
```

另一个固定策略包装器只接受用户在服务目录明确授权后的白名单同步：

```text
add SERVICE.service
remove SERVICE.service
```

包装器会同时检查动作、systemd 单元格式、root 持有且不可写的
`/etc/aioms/service-control.allowlist`，并在操作前后核对服务状态。sudoers 只授权这个包装器，
不会授权任意 `systemctl` 或 Shell。

`aioms-service-policy` 加入服务前会核对单元已经由 systemd 加载，并以文件锁和同目录临时
文件原子更新 root 持有的白名单；删除操作可重复执行。它不执行服务启停，实际控制仍必须经过
`aioms-service-control` 和独立的操作审批。

白名单由目标服务器 `deploy.conf` 的 `SERVICE_CONTROL_ALLOWLIST` 配置，例如：

```ini
SERVICE_CONTROL_ALLOWLIST=bt.service,nginx.service
```

留空表示拒绝全部服务控制。修改白名单后需要由服务器管理员重新运行 `deploy.sh`。
