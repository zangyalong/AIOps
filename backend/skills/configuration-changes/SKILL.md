---
name: configuration-changes
description: 安全预览并修改目标服务器上的普通文本、dotenv、JSON 或 YAML 配置，保持文件修改与服务生效动作分离。
version: 1.0.0
category: 配置变更
triggers:
  - .env
  - dotenv
  - yaml
  - json
  - 配置文件
  - 修改配置
  - 修改文件
  - 改端口
  - proxy_pass
tools:
  - investigate
  - grant_server_file_write_access
  - update_server_file
  - patch_server_config
---

# 配置文件变更

## 选择正确预览

- 修改 dotenv、JSON 或 YAML 的一个已有非敏感标量：使用 `investigate(area="change", query="config-patch-preview")`。dotenv 的 `selector` 是变量名，JSON/YAML 的 selector 是 JSON Pointer。
- 完整替换普通文本：使用 `file-update-preview`。它接收完整新内容，不适合只改结构化配置中的一个字段。
- 不确定文件路径时先用 `find-files` 查找，再用 `read-file` 核对；不得猜路径。

预览返回的路径、哈希、selector、diff 和其他映射必须原样传给对应写工具。`preview_changed` 表示文件在预览后变化，只能重新预览并重新申请一次；相同变化再次发生就停止。

## 授权与生效

预览明确返回未授权时，先核对服务目录实例和既存文件，再单独调用 `grant_server_file_write_access` 申请精确文件授权；不能授权目录或不存在的文件。授权成功后重新预览，实际修改仍是另一个审批操作。

文件工具只改文件，不会校验配置、reload、重启或部署。只有用户明确要求生效时，才在写入成功后选择对应运行时 Skill，另行审批生效动作。密码、Token、密钥不得作为结构化补丁值进入模型或工具参数。
