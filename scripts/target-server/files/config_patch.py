#!/usr/bin/python3.11
"""在目标机本地生成 dotenv / JSON / YAML 标量补丁，真实配置不会返回后端。"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

MAX_FILE_BYTES = 65_536
MAX_VALUE_BYTES = 4_096

_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_SENSITIVE_NAME = re.compile(
    r"password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|credential|authorization",
    re.IGNORECASE,
)
_URL_CREDENTIALS = re.compile(r"[a-z][a-z0-9+.-]*://[^:/\s]+:[^@/\s]+@", re.IGNORECASE)
_SENSITIVE_VALUE_ASSIGNMENT = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|credential|authorization)\s*[:=]",
    re.IGNORECASE,
)
_YAML_MAP_TAG = "tag:yaml.org,2002:map"
_YAML_SEQUENCE_TAG = "tag:yaml.org,2002:seq"
_YAML_STRING_TAG = "tag:yaml.org,2002:str"
_YAML_SCALAR_TAGS = {
    _YAML_STRING_TAG,
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:float",
    "tag:yaml.org,2002:bool",
    "tag:yaml.org,2002:null",
}


class ConfigPatchError(ValueError):
    pass


@dataclass(frozen=True)
class JsonNode:
    value: object
    start: int
    end: int
    children: dict[str, JsonNode] | list[JsonNode] | None = None


class JsonDocument:
    def __init__(self, text: str) -> None:
        self.text = text
        self.length = len(text)

    def parse(self) -> JsonNode:
        node, end = self._value(self._space(0))
        if self._space(end) != self.length:
            raise ConfigPatchError("JSON 根值之后存在多余内容")
        return node

    def _space(self, position: int) -> int:
        while position < self.length and self.text[position] in " \t\r\n":
            position += 1
        return position

    def _value(self, position: int) -> tuple[JsonNode, int]:
        position = self._space(position)
        if position >= self.length:
            raise ConfigPatchError("JSON 值不完整")
        first = self.text[position]
        if first == '"':
            value, end = json.decoder.scanstring(self.text, position + 1, True)
            return JsonNode(value, position, end), end
        if first == "{":
            return self._object(position)
        if first == "[":
            return self._array(position)
        for literal, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(literal, position):
                end = position + len(literal)
                return JsonNode(value, position, end), end
        match = _JSON_NUMBER.match(self.text, position)
        if match is None:
            raise ConfigPatchError(f"JSON 在字符 {position} 附近格式不合法")
        raw = match.group(0)
        value = json.loads(raw)
        return JsonNode(value, position, match.end()), match.end()

    def _object(self, start: int) -> tuple[JsonNode, int]:
        position = self._space(start + 1)
        values: dict[str, object] = {}
        children: dict[str, JsonNode] = {}
        if position < self.length and self.text[position] == "}":
            return JsonNode(values, start, position + 1, children), position + 1
        while True:
            if position >= self.length or self.text[position] != '"':
                raise ConfigPatchError("JSON 对象键必须是字符串")
            key, position = json.decoder.scanstring(self.text, position + 1, True)
            if key in children:
                raise ConfigPatchError(f"JSON 对象包含重复键：{key}")
            position = self._space(position)
            if position >= self.length or self.text[position] != ":":
                raise ConfigPatchError("JSON 对象键后缺少冒号")
            child, position = self._value(position + 1)
            values[key] = child.value
            children[key] = child
            position = self._space(position)
            if position < self.length and self.text[position] == ",":
                position = self._space(position + 1)
                continue
            if position < self.length and self.text[position] == "}":
                end = position + 1
                return JsonNode(values, start, end, children), end
            raise ConfigPatchError("JSON 对象缺少逗号或右花括号")

    def _array(self, start: int) -> tuple[JsonNode, int]:
        position = self._space(start + 1)
        values: list[object] = []
        children: list[JsonNode] = []
        if position < self.length and self.text[position] == "]":
            return JsonNode(values, start, position + 1, children), position + 1
        while True:
            child, position = self._value(position)
            values.append(child.value)
            children.append(child)
            position = self._space(position)
            if position < self.length and self.text[position] == ",":
                position = self._space(position + 1)
                continue
            if position < self.length and self.text[position] == "]":
                end = position + 1
                return JsonNode(values, start, end, children), end
            raise ConfigPatchError("JSON 数组缺少逗号或右方括号")


def _fail(message: str) -> NoReturn:
    print(f"config-patch: {message}", file=sys.stderr)
    raise SystemExit(2)


def _load_scalar(raw: str) -> str | int | float | bool | None:
    try:
        value = json.loads(
            raw,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ConfigPatchError(f"不支持 JSON 常量 {constant}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise ConfigPatchError("value 必须编码为 JSON 标量") from exc
    if isinstance(value, (dict, list)):
        raise ConfigPatchError("只允许更新字符串、数字、布尔值或 null 标量")
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigPatchError("数字必须是有限值")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
            raise ConfigPatchError(f"字符串值不能超过 {MAX_VALUE_BYTES} 字节")
        if any(char in value for char in ("\x00", "\n", "\r")):
            raise ConfigPatchError("字符串值不能包含空字符或换行")
        if _looks_sensitive_value(value):
            raise ConfigPatchError("新值疑似包含密钥或带密码连接串")
    return value


def _looks_sensitive_value(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return (
        "-----BEGIN " in value
        or _URL_CREDENTIALS.search(value) is not None
        or _SENSITIVE_VALUE_ASSIGNMENT.search(value) is not None
        or value.lower().startswith(("bearer ", "basic "))
    )


def _validate_selector_names(parts: list[str]) -> None:
    sensitive = next((part for part in parts if _SENSITIVE_NAME.search(part)), None)
    if sensitive is not None:
        raise ConfigPatchError(f"选择器包含敏感字段 {sensitive}，禁止通过 Agent 修改")


def _dotenv_patch(source: bytes, selector: str, value: object) -> tuple[bytes, object]:
    if not _ENV_KEY.fullmatch(selector):
        raise ConfigPatchError("dotenv 选择器必须是合法环境变量名")
    _validate_selector_names([selector])
    if not isinstance(value, str):
        raise ConfigPatchError("dotenv 新值必须是字符串")
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigPatchError("dotenv 文件必须是 UTF-8 文本") from exc
    assignment = re.compile(
        rf"^(?P<prefix>[ \t]*(?:export[ \t]+)?{re.escape(selector)}[ \t]*=[ \t]*)(?P<value>.*)$"
    )
    matches: list[tuple[int, re.Match[str], str]] = []
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        match = assignment.fullmatch(body)
        if match is not None:
            matches.append((index, match, ending))
    if not matches:
        raise ConfigPatchError(f"dotenv 中不存在变量 {selector}，不会自动新增")
    if len(matches) != 1:
        raise ConfigPatchError(f"dotenv 中变量 {selector} 出现多次")
    index, match, ending = matches[0]
    before = match.group("value")
    if _looks_sensitive_value(before):
        raise ConfigPatchError(f"变量 {selector} 的当前值疑似敏感，禁止返回预览")
    lines[index] = f"{match.group('prefix')}{value}{ending}"
    return "".join(lines).encode("utf-8"), before


def _pointer_parts(selector: str, format_label: str = "JSON") -> list[str]:
    if not selector.startswith("/"):
        raise ConfigPatchError(
            f"{format_label} 选择器必须是非空 JSON Pointer，例如 /server/port"
        )
    if any(char in selector for char in ("\x00", "\n", "\r")):
        raise ConfigPatchError("JSON Pointer 不能包含空字符或换行")
    parts: list[str] = []
    for raw in selector[1:].split("/"):
        if re.search(r"~(?:[^01]|$)", raw):
            raise ConfigPatchError("JSON Pointer 中 ~ 只能写成 ~0 或 ~1")
        parts.append(raw.replace("~1", "/").replace("~0", "~"))
    _validate_selector_names(parts)
    return parts


def _json_child(node: JsonNode, part: str) -> JsonNode:
    if isinstance(node.children, dict):
        try:
            return node.children[part]
        except KeyError as exc:
            raise ConfigPatchError(f"JSON Pointer 字段不存在：{part}") from exc
    if isinstance(node.children, list):
        if not re.fullmatch(r"0|[1-9][0-9]*", part):
            raise ConfigPatchError(f"JSON 数组下标不合法：{part}")
        index = int(part)
        if index >= len(node.children):
            raise ConfigPatchError(f"JSON 数组下标越界：{part}")
        return node.children[index]
    raise ConfigPatchError("JSON Pointer 不能继续进入标量值")


def _resolve_json_node(root: JsonNode, parts: list[str]) -> JsonNode:
    target = root
    for part in parts:
        target = _json_child(target, part)
    return target


def _set_python_value(document: object, parts: list[str], value: object) -> None:
    parent = document
    for part in parts[:-1]:
        if isinstance(parent, dict):
            parent = parent[part]
        elif isinstance(parent, list):
            parent = parent[int(part)]
        else:
            raise ConfigPatchError("JSON Pointer 不能继续进入标量值")
    leaf = parts[-1]
    if isinstance(parent, dict):
        parent[leaf] = value
    elif isinstance(parent, list):
        parent[int(leaf)] = value
    else:
        raise ConfigPatchError("JSON Pointer 的父节点不是容器")


def _json_patch(source: bytes, selector: str, value: object) -> tuple[bytes, object]:
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigPatchError("JSON 文件必须是 UTF-8 文本") from exc
    root = JsonDocument(text).parse()
    parts = _pointer_parts(selector)
    target = _resolve_json_node(root, parts)
    if isinstance(target.value, (dict, list)):
        raise ConfigPatchError("只允许替换已有 JSON 标量，不能替换对象或数组")
    if _looks_sensitive_value(target.value):
        raise ConfigPatchError("目标 JSON 字段的当前值疑似敏感，禁止返回预览")
    replacement = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    updated_text = f"{text[: target.start]}{replacement}{text[target.end :]}"
    try:
        actual = json.loads(updated_text)
    except json.JSONDecodeError as exc:
        raise ConfigPatchError("内部 JSON 补丁校验失败") from exc
    expected = copy.deepcopy(root.value)
    _set_python_value(expected, parts, value)
    actual_root = JsonDocument(updated_text).parse()
    actual_target = _resolve_json_node(actual_root, parts)
    if actual != expected or not (
        type(actual_target.value) is type(value) and actual_target.value == value
    ):
        raise ConfigPatchError("JSON 补丁改变了选择器以外的语义")
    return updated_text.encode("utf-8"), target.value


def _validate_yaml_tree(root: Node) -> None:
    seen: set[int] = set()

    def visit(node: Node) -> None:
        identity = id(node)
        if identity in seen:
            raise ConfigPatchError(
                "YAML 包含别名引用，无法保证单路径补丁不会影响其他字段"
            )
        seen.add(identity)
        if isinstance(node, MappingNode):
            if node.tag != _YAML_MAP_TAG:
                raise ConfigPatchError(f"YAML 包含不支持的映射标签：{node.tag}")
            keys: set[str] = set()
            for key_node, value_node in node.value:
                if not isinstance(key_node, ScalarNode) or key_node.tag != _YAML_STRING_TAG:
                    raise ConfigPatchError("YAML 映射键必须是字符串")
                if key_node.value == "<<":
                    raise ConfigPatchError(
                        "YAML 包含合并键，无法保证单路径补丁语义"
                    )
                if key_node.value in keys:
                    raise ConfigPatchError(f"YAML 映射包含重复键：{key_node.value}")
                keys.add(key_node.value)
                visit(key_node)
                visit(value_node)
            return
        if isinstance(node, SequenceNode):
            if node.tag != _YAML_SEQUENCE_TAG:
                raise ConfigPatchError(f"YAML 包含不支持的序列标签：{node.tag}")
            for child in node.value:
                visit(child)
            return
        if isinstance(node, ScalarNode):
            if node.tag not in _YAML_SCALAR_TAGS:
                raise ConfigPatchError(f"YAML 包含不支持的标量标签：{node.tag}")
            return
        raise ConfigPatchError("YAML 包含不支持的节点类型")

    visit(root)


def _yaml_child(node: Node, part: str) -> Node:
    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            if key_node.value == part:
                return value_node
        raise ConfigPatchError(f"YAML Pointer 字段不存在：{part}")
    if isinstance(node, SequenceNode):
        if not re.fullmatch(r"0|[1-9][0-9]*", part):
            raise ConfigPatchError(f"YAML 数组下标不合法：{part}")
        index = int(part)
        if index >= len(node.value):
            raise ConfigPatchError(f"YAML 数组下标越界：{part}")
        return node.value[index]
    raise ConfigPatchError("YAML Pointer 不能继续进入标量值")


def _resolve_yaml_node(root: Node, parts: list[str]) -> Node:
    target = root
    for part in parts:
        target = _yaml_child(target, part)
    return target


def _same_tree(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        assert isinstance(right, dict)
        return left.keys() == right.keys() and all(
            _same_tree(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        assert isinstance(right, list)
        return len(left) == len(right) and all(
            _same_tree(left_value, right_value)
            for left_value, right_value in zip(left, right, strict=True)
        )
    return left == right


def _yaml_patch(source: bytes, selector: str, value: object) -> tuple[bytes, object]:
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigPatchError("YAML 文件必须是 UTF-8 文本") from exc
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise ConfigPatchError(f"YAML 格式不合法：{exc}") from exc
    if root is None:
        raise ConfigPatchError("YAML 文件不能为空")
    _validate_yaml_tree(root)
    parts = _pointer_parts(selector, "YAML")
    target = _resolve_yaml_node(root, parts)
    if not isinstance(target, ScalarNode):
        raise ConfigPatchError("只允许替换已有 YAML 标量，不能替换映射或序列")
    if target.style in {"|", ">"}:
        raise ConfigPatchError("不支持替换 YAML 多行块标量")
    try:
        before = yaml.safe_load(text[target.start_mark.index : target.end_mark.index])
    except yaml.YAMLError as exc:
        raise ConfigPatchError("无法解析目标 YAML 标量") from exc
    if isinstance(before, (dict, list)) or not isinstance(
        before, (str, int, float, bool, type(None))
    ):
        raise ConfigPatchError("目标 YAML 值不是受支持的标量")
    if _looks_sensitive_value(before):
        raise ConfigPatchError("目标 YAML 字段的当前值疑似敏感，禁止返回预览")
    replacement = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    updated_text = (
        f"{text[: target.start_mark.index]}{replacement}{text[target.end_mark.index :]}"
    )
    try:
        expected = yaml.safe_load(text)
        actual = yaml.safe_load(updated_text)
        actual_root = yaml.compose(updated_text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise ConfigPatchError("内部 YAML 补丁校验失败") from exc
    if actual_root is None:
        raise ConfigPatchError("内部 YAML 补丁校验失败")
    _validate_yaml_tree(actual_root)
    expected = copy.deepcopy(expected)
    _set_python_value(expected, parts, value)
    actual_target = _resolve_yaml_node(actual_root, parts)
    actual_value = yaml.safe_load(
        updated_text[actual_target.start_mark.index : actual_target.end_mark.index]
    )
    if not _same_tree(actual, expected) or not (
        type(actual_value) is type(value) and actual_value == value
    ):
        raise ConfigPatchError("YAML 补丁改变了选择器以外的语义")
    return updated_text.encode("utf-8"), before


def _patch(
    source_path: Path, config_format: str, selector: str, value: object
) -> tuple[bytes, dict[str, object]]:
    source = source_path.read_bytes()
    if len(source) > MAX_FILE_BYTES:
        raise ConfigPatchError(f"配置文件不能超过 {MAX_FILE_BYTES} 字节")
    filename = source_path.name
    if config_format == "dotenv":
        if not (
            filename == ".env"
            or filename.startswith(".env.")
            or filename.endswith(".env")
        ):
            raise ConfigPatchError("dotenv 驱动只允许 .env、.env.* 或 *.env 文件")
        updated, before = _dotenv_patch(source, selector, value)
    elif config_format == "json":
        if not filename.endswith(".json"):
            raise ConfigPatchError("JSON 驱动只允许 .json 文件")
        updated, before = _json_patch(source, selector, value)
    elif config_format == "yaml":
        if not filename.endswith((".yaml", ".yml")):
            raise ConfigPatchError("YAML 驱动只允许 .yaml 或 .yml 文件")
        updated, before = _yaml_patch(source, selector, value)
    else:
        raise ConfigPatchError("format 只支持 dotenv、json 或 yaml")
    result = {
        "format": config_format,
        "selector": selector,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "output_sha256": hashlib.sha256(updated).hexdigest(),
        "before": before,
        "after": value,
        "changed": source != updated,
    }
    return updated, result


def _write_output(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def main() -> None:
    if len(sys.argv) not in {6, 7}:
        _fail(
            "用法：config_patch.py preview SOURCE FORMAT SELECTOR VALUE_JSON | "
            "apply SOURCE OUTPUT FORMAT SELECTOR VALUE_JSON"
        )
    mode = sys.argv[1]
    if mode == "preview" and len(sys.argv) == 6:
        source_path = Path(sys.argv[2])
        config_format, selector, raw_value = sys.argv[3:6]
        output_path = None
    elif mode == "apply" and len(sys.argv) == 7:
        source_path = Path(sys.argv[2])
        output_path = Path(sys.argv[3])
        config_format, selector, raw_value = sys.argv[4:7]
    else:
        _fail("mode 和参数数量不匹配")
    try:
        value = _load_scalar(raw_value)
        updated, result = _patch(source_path, config_format, selector, value)
        if output_path is not None:
            _write_output(output_path, updated)
    except (
        ConfigPatchError,
        OSError,
        RecursionError,
        UnicodeError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as exc:
        _fail(str(exc))
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
