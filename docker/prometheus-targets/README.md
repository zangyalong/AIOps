# Prometheus 本地抓取目标

这个目录用于保存当前环境的真实 Prometheus 抓取地址。真实 IP、主机名和业务标识属于环境配置，
不能写进受 Git 跟踪的 `docker/prometheus.yml`。

首次配置：

```bash
cp docker/prometheus-targets/managed-targets.yml.example \
  docker/prometheus-targets/managed-targets.yml
```

然后只编辑 `managed-targets.yml`，把文档保留地址和示例标签替换成当前环境的真实值。该文件匹配
仓库根目录 `.gitignore`，Prometheus 会通过 `file_sd_configs` 每 30 秒自动重新读取，无需修改主配置。

提交前可以运行：

```bash
cd backend && uv run python -m unittest tests.test_repository_hygiene -v
```
