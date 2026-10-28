#!/bin/bash

set -euo pipefail

fail() {
    echo "alloy/check.sh: $*" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || fail "请使用 root 或 sudo 运行"
command -v alloy >/dev/null 2>&1 || fail "没有找到 alloy 命令"
[[ -f /etc/alloy/config.alloy ]] || fail "没有找到 /etc/alloy/config.alloy"

echo "1/4 校验 Alloy 配置语法"
alloy validate /etc/alloy/config.alloy

echo "2/4 检查 systemd 服务状态"
systemctl is-active --quiet alloy || fail "alloy.service 不是 active"
systemctl --no-pager --full status alloy

echo "3/4 检查本机 Alloy HTTP 健康端点"
if command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error http://127.0.0.1:12345/-/ready
    echo
else
    echo "未安装 curl，跳过 127.0.0.1:12345/-/ready；Alloy UI 默认只监听本机。"
fi

echo "4/4 显示最近 Alloy 日志"
journalctl -u alloy --since "10 minutes ago" --no-pager -n 100

echo "本机检查完成。还需要从中央 Loki 查询 service/server 标签确认日志已成功送达。"
