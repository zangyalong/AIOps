"""Agent 工具入参的共享约束与模型出站 Schema 构造。"""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any, Iterable

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SERVER_IDENTIFIER_PATTERN = r"^[^\x00\r\n]{1,100}$"
CATALOG_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,199}$"
ENVIRONMENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$"
RUNTIME_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,199}$"
LOG_SERVICE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$"
CONTAINER_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
ABSOLUTE_PATH_PATTERN = r"^/[^\x00\r\n]*$"
FILENAME_PATTERN_PATTERN = r"^[A-Za-z0-9_.@*?-]{1,100}$"
SHA256_PATTERN = r"^[a-f0-9]{64}$"


class ToolInputModel(BaseModel):
    """所有模型可见工具入参的基类；未知字段必须显式报错。"""

    model_config = ConfigDict(extra="forbid")


ServerIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=SERVER_IDENTIFIER_PATTERN,
    ),
    Field(
        description="已纳管服务器名称或 IP；优先使用服务器列表返回的名称。",
        examples=["web-01"],
    ),
]

CatalogServiceName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=200,
        pattern=CATALOG_NAME_PATTERN,
    ),
    Field(
        description=(
            "服务目录中的逻辑服务名；只能包含字母、数字、点、下划线、连字符、@ 或冒号。"
        ),
        examples=["orders-api"],
    ),
]

EnvironmentName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=50,
        pattern=ENVIRONMENT_PATTERN,
    ),
    Field(
        description="部署环境；只能包含字母、数字、点、下划线或连字符。",
        examples=["prod"],
    ),
]

RuntimeName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=200,
        pattern=RUNTIME_NAME_PATTERN,
    ),
    Field(
        description="发现结果原样返回的 systemd 单元、Docker 容器或进程运行时名称。",
        examples=["orders-api.service"],
    ),
]

LogServiceName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=200,
        pattern=LOG_SERVICE_NAME_PATTERN,
    ),
    Field(
        description="日志配置预览中返回的逻辑服务名，必须原样选择。",
        examples=["orders-api"],
    ),
]

ContainerName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=CONTAINER_NAME_PATTERN,
    ),
    Field(
        description="服务目录中已登记且已授权的 Docker 容器名，不是 Compose 路径。",
        examples=["orders-api-1"],
    ),
]

AbsolutePath = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=4096,
        pattern=ABSOLUTE_PATH_PATTERN,
    ),
    Field(
        description="目标服务器上的绝对路径，必须以 / 开头且不能包含空字符或换行。",
        examples=["/opt/app/.env"],
    ),
]

FilenamePattern = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=FILENAME_PATTERN_PATTERN,
    ),
    Field(
        description="文件名匹配模式，只允许字母、数字和 ._@*?-。",
        examples=["*.conf"],
    ),
]

Sha256Digest = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=64,
        max_length=64,
        pattern=SHA256_PATTERN,
    ),
    Field(description="由后端生成的 64 位小写 SHA-256 十六进制摘要。"),
]


def _schema_dict(schema: type[BaseModel] | dict[str, Any]) -> dict[str, Any]:
    return (
        deepcopy(schema)
        if isinstance(schema, dict)
        else deepcopy(schema.model_json_schema())
    )


def model_tool_parameters(tool: BaseTool) -> dict[str, Any]:
    """构造真正发给模型的参数 Schema，并隐藏运行时注入字段。

    LangChain 会为 ``tool_call_schema`` 重建一个 Pydantic 子模型；这个过程会丢失原模型的
    ``json_schema_extra``、示例和 ``extra=forbid``。这里以原始 ``args_schema`` 为准，
    只借助 ``tool_call_schema`` 判定哪些顶层字段允许由模型填写。
    """

    input_schema = tool.get_input_schema()
    if not isinstance(input_schema, type) or not issubclass(input_schema, BaseModel):
        raise TypeError(f"工具 {tool.name} 必须使用显式 Pydantic args_schema")

    full_schema = _schema_dict(input_schema)
    visible_schema = _schema_dict(tool.tool_call_schema)
    visible_names = set(visible_schema.get("properties", {}))
    full_properties = full_schema.get("properties", {})
    full_schema["properties"] = {
        name: schema
        for name, schema in full_properties.items()
        if name in visible_names
    }
    if "required" in full_schema:
        full_schema["required"] = [
            name for name in full_schema["required"] if name in visible_names
        ]
        if not full_schema["required"]:
            full_schema.pop("required")
    full_schema["title"] = tool.name
    return full_schema


def model_tool_definition(tool: BaseTool) -> dict[str, Any]:
    """返回 OpenAI 兼容的函数工具定义，运行时执行仍使用原 ``BaseTool``。"""

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": model_tool_parameters(tool),
        },
    }


def model_tool_definitions(tools: Iterable[BaseTool]) -> tuple[dict[str, Any], ...]:
    return tuple(model_tool_definition(tool) for tool in tools)
