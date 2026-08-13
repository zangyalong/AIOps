---
name: systemd-operations
description: 按 unit 状态、依赖、退出原因、journal 和监听端口调查 systemd 服务故障。
---

# systemd 服务排障

## 调查顺序

1. 确认完整 unit 名以及它是 service、socket、timer 还是模板实例，不凭应用名猜测。
2. 分别检查 `LoadState`、`ActiveState`、`SubState`、主进程 PID 和最近退出状态。
3. 查看 unit 定义来源、启动命令、环境文件、依赖和 ordering，区分“没有启用”和“当前没有运行”。
4. 从本次启动时间附近读取 journal，优先看第一条根因错误，而不是最后一串连锁失败。
5. 对监听型服务再核对 TCP/UDP 或 Unix socket，避免只凭 systemd 的 active 状态判断可用。

常用只读命令包括：

```bash
systemctl status <unit> --no-pager
systemctl show <unit>
systemctl is-active <unit>
systemctl is-enabled <unit>
systemctl list-dependencies <unit>
journalctl -u <unit> -n 100 --no-pager
```

在 AIOps 对话里，应把这些命令映射为当前可用的只读调查能力，不假定存在任意 systemctl Shell。

## 常见现象

- `active (exited)` 可能表示一次性任务成功完成，不等价于守护进程仍在运行。
- `activating (auto-restart)` 通常需要结合退出码和重启节流日志判断崩溃循环。
- unit 文件已修改但行为没变时，要确认 systemd 是否重新加载了定义，以及进程是否重新启动。
- socket activation 场景里 service 未运行可能是正常状态，应同时检查对应 socket unit。
- `is-enabled` 和 `is-active` 回答的是不同问题，不应互相替代。

涉及启停、重启、reload 或 unit 修改时，以固定运维核心与安全边界为准；本 Skill 只提供调查方法。
