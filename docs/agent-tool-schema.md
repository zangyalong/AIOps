# Agent 工具入参 Schema 设计

## 问题

模型调用工具时只能看到工具名称、描述和 JSON Schema。若限制只写在执行器的正则或
Pydantic 自定义 validator 里，模型不知道限制，错误参数会一直到执行阶段才失败；写工具还可能
先生成一张实际上无法执行的审批卡。

本项目还使用 LangChain。它发送给模型的 `tool_call_schema` 会为隐藏运行时参数重新创建
Pydantic 子模型，这个过程中可能丢失原始模型的 `json_schema_extra`、字段示例和
`extra=forbid`，因此只测试 `args_schema.model_json_schema()` 并不能证明约束真正到达模型。

## 同类项目做法

- [Pi Agent](https://github.com/badlogic/pi-mono/blob/main/packages/ai/README.md) 使用 TypeBox
  同时生成 JSON Schema 和运行时校验；Agent Loop 在执行前统一校验，失败作为工具错误返回模型，
  允许模型修正后重试。扩展工具的
  [ToolDefinition](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/src/core/extensions/types.ts)
  也把 `parameters` 定义为唯一的 TypeBox 契约。
- [LangChain](https://docs.langchain.com/oss/python/langchain/tools) 推荐复杂工具显式提供
  Pydantic `args_schema`，用字段类型、`Field` 描述和约束生成工具 Schema并执行输入校验。
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/tools/) 同样从 Pydantic
  生成参数 JSON Schema，并明确支持在 `Field` 中声明长度、范围和正则；其
  [严格 Schema](https://openai.github.io/openai-agents-python/ref/function_schema/) 默认开启，
  用来提高模型生成正确参数的概率。
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/zh-cn/guides/tool_calls) 支持
  `pattern` 和 `additionalProperties=false`，同时明确要求应用在调用真实函数前自行校验模型
  参数。它的 strict 模式目前属于 Beta，且不支持 `minLength`、`maxLength`、`minItems`、
  `maxItems`；本项目暂不切换 Beta 地址，继续由 Broker 完成确定性校验。

共同点不是“只相信模型会遵守 Schema”，而是：同一份类型契约既发给模型，又在执行前校验；
校验失败作为可恢复错误进入 Agent Loop；数据库状态、安全策略等 JSON Schema 无法表达的约束，
继续由语义解析器和执行器负责。

## AIOps 的三层实现

1. `ToolInputModel` 统一禁止未知字段；服务器、环境、运行时名称、绝对路径等常用字段使用共享
   约束类型。每个业务工具仍有自己的显式 Pydantic 入参模型。
2. 模型绑定前以原始 `args_schema` 生成最终工具定义，保留正则、示例和
   `additionalProperties=false`；再按 LangChain 的注入标记剔除哈希、运行时 ID 等后端字段。
3. Operation Broker 在审批前用同一 Pydantic 模型规范化参数。结构错误返回 Agent 自行纠正；
   服务是否存在、PromQL 是否有效、文件是否获授权等动态约束由审批前解析器和执行器继续复核。

契约测试检查的是最终出站 Schema，而不是只检查源 Pydantic 模型，防止以后升级 LangChain 或
新增工具时再次出现“后端有规则、模型看不到”的回归。
