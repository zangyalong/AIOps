---
name: systemd-operations
description: 调查 systemd unit 的活动状态、启用状态和 journal，并通过受控工具启停或重启已授权服务。
version: 1.0.0
category: Linux 服务
triggers:
  - systemd
  - systemctl
  - journalctl
  - unit
  - daemon-reload
  - service 文件
  - 服务启动
  - 服务停止
  - 服务重启
tools:
  - investigate
  - start_service
  - stop_service
  - restart_service
---

# systemd 运维

## 调查顺序

1. 不确定 unit 名时先用 `investigate(area="services", query="discover")` 发现准确运行对象，不凭应用名猜测 `.service` 名称。
2. 总览使用 `runtime.query="services"`，失败单元使用 `failed-services`。
3. 针对单元依次使用 `service:<完整 unit>`、`service-active:<完整 unit>`、`service-enabled:<完整 unit>`；日志使用 `journal:<完整 unit>`。
4. 端口或 Unix socket 相关服务，再分别调查 `sockets` 或 `unix-sockets`。

这些选择器对应常见只读方法 `systemctl status`、`systemctl is-active`、`systemctl is-enabled`、`systemctl --failed` 与 `journalctl -u <unit> -n 100 --no-pager`。在 AIOMS 对话中通过 `investigate` 使用，避免暴露内部执行器。

## 受控变更

- 用户明确要求后，使用 `start_service`、`stop_service` 或 `restart_service`，参数 `runtime_type="systemd"`，并传服务目录中已授权的完整 unit 名。
- 每个写操作单独审批。批准只代表允许尝试，必须以工具返回和随后的状态复核判断是否真正成功。
- 修改 unit 文件与加载 unit 是两件独立操作。当前没有登记 `daemon-reload` 写能力时，不得假装通过重启工具完成了 `systemctl daemon-reload`，应准确说明缺少该受控动作。

## 禁区

不要使用或建议任意 `systemctl` 写命令、`systemctl edit`、修改 `/etc/systemd/system`、`enable/disable/mask`，也不要通过 shell、sudo 或替代 unit 名绕过服务目录和目标机白名单。
