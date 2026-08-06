# Prometheus 规则目录

`managed_alert_rules.yml` 由 AIOMS 后端根据 PostgreSQL 的 `alert_rules` 表自动生成，
仅作为 Prometheus 的运行时输入，不是用户或开发者手工维护的配置文件，因此不会提交到 Git。

用户通过前端“预警规则”页面新增、修改、启停或删除规则。后端启动以及页面应用规则时，
都会重新生成该文件并请求 Prometheus 热重载。
