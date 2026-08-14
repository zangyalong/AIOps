from __future__ import annotations

import unittest
from typing import Any

from pydantic import BaseModel

from app.agents.tools import catalog
from app.agents.tools.tool_schema import (
    ABSOLUTE_PATH_PATTERN,
    CONTAINER_NAME_PATTERN,
    model_tool_definition,
    model_tool_parameters,
)


def _resolve(schema: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = definitions[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _missing_descriptions(
    schema: dict[str, Any],
    definitions: dict[str, Any],
    *,
    path: str = "",
) -> list[str]:
    schema = _resolve(schema, definitions)
    missing: list[str] = []
    for name, property_schema in schema.get("properties", {}).items():
        resolved = _resolve(property_schema, definitions)
        if not property_schema.get("description") and not resolved.get("description"):
            missing.append(f"{path}{name}")
        missing.extend(
            _missing_descriptions(
                property_schema,
                definitions,
                path=f"{path}{name}.",
            )
        )
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        missing.extend(
            _missing_descriptions(item_schema, definitions, path=f"{path}[].")
        )
    for branch in schema.get("anyOf", []):
        if isinstance(branch, dict):
            missing.extend(_missing_descriptions(branch, definitions, path=path))
    return missing


class AgentToolSchemaContractTest(unittest.TestCase):
    def test_every_registered_tool_uses_closed_described_pydantic_schema(self) -> None:
        for name, tool in catalog.TOOLS_BY_NAME.items():
            with self.subTest(tool=name):
                input_schema = tool.get_input_schema()
                self.assertIsInstance(input_schema, type)
                self.assertTrue(issubclass(input_schema, BaseModel))

                schema = model_tool_parameters(tool)
                self.assertIs(schema.get("additionalProperties"), False)
                for definition_name, definition in schema.get("$defs", {}).items():
                    if "properties" in definition:
                        self.assertIs(
                            definition.get("additionalProperties"),
                            False,
                            definition_name,
                        )
                self.assertEqual(
                    _missing_descriptions(schema, schema.get("$defs", {})),
                    [],
                )

    def test_model_definition_preserves_constraints_lost_by_langchain_subset(
        self,
    ) -> None:
        tool = catalog.WRITE_TOOLS_BY_NAME["create_alert_rule"]
        langchain_schema = tool.tool_call_schema.model_json_schema()
        outbound = model_tool_definition(tool)["function"]["parameters"]

        self.assertNotIn("pattern", langchain_schema["properties"]["name"])
        self.assertEqual(
            outbound["properties"]["name"]["pattern"],
            "^[A-Za-z_][A-Za-z0-9_]{0,199}$",
        )
        self.assertEqual(
            outbound["properties"]["name"]["examples"],
            ["NodeCpuUsageHigh"],
        )
        self.assertIs(outbound["additionalProperties"], False)

    def test_runtime_injected_fields_are_hidden_from_model(self) -> None:
        expected_fields = {
            "start_service": {
                "server_name",
                "service_name",
                "environment",
                "instance_name",
            },
            "apply_server_log_config": {"server_name", "mappings"},
            "update_server_file": {"server_name", "path", "new_content"},
            "patch_server_config": {
                "server_name",
                "path",
                "config_format",
                "selector",
                "value",
            },
        }
        for name, fields in expected_fields.items():
            with self.subTest(tool=name):
                schema = model_tool_parameters(catalog.TOOLS_BY_NAME[name])
                self.assertEqual(set(schema["properties"]), fields)

    def test_formerly_hidden_executor_constraints_are_model_visible(self) -> None:
        recreate = model_tool_parameters(
            catalog.WRITE_TOOLS_BY_NAME["recreate_compose_service"]
        )
        self.assertEqual(
            recreate["properties"]["container_name"]["pattern"],
            CONTAINER_NAME_PATTERN,
        )

        grant = model_tool_parameters(
            catalog.WRITE_TOOLS_BY_NAME["grant_server_file_write_access"]
        )
        self.assertEqual(
            grant["properties"]["path"]["pattern"],
            ABSOLUTE_PATH_PATTERN,
        )

        registration = model_tool_parameters(
            catalog.WRITE_TOOLS_BY_NAME["register_server_services"]
        )
        item_ref = registration["properties"]["services"]["items"]["$ref"]
        item_schema = registration["$defs"][item_ref.rsplit("/", 1)[-1]]
        self.assertIn("pattern", item_schema["properties"]["service_name"])
        self.assertIn("pattern", item_schema["properties"]["runtime_name"])

    def test_unknown_top_level_field_is_rejected_before_execution(self) -> None:
        schema = catalog.WRITE_TOOLS_BY_NAME[
            "deploy_server_environment"
        ].get_input_schema()
        with self.assertRaisesRegex(ValueError, "extra_forbidden"):
            schema.model_validate(
                {"server_name": "web-01", "invented_parameter": "value"}
            )


if __name__ == "__main__":
    unittest.main()
