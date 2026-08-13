from __future__ import annotations

import unittest

from app.agents.presentation import extract_http_urls, restore_truncated_tool_links


class ToolLinkPresentationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original = (
            "http://localhost:3000/explore?schemaVersion=1&"
            "panes=%7B%22aioms%22%3A%7B%22datasource%22%3A%22prometheus%22%7D%7D"
            "&orgId=1"
        )

    def test_extracts_complete_tool_url_without_sentence_punctuation(self) -> None:
        urls = extract_http_urls([f"已生成链接：\n{self.original}。"])

        self.assertEqual(urls, (self.original,))

    def test_restores_truncated_markdown_href_from_original_tool_output(self) -> None:
        answer = (
            "查看入口：[http://localhost:3000/explore?...orgId=1]"
            "(http://localhost:3000/explore?schemaVersion=1&panes=%7B...orgId=1)"
        )

        restored = restore_truncated_tool_links(
            answer,
            [f"已生成 Grafana 指标走势图链接：\n{self.original}"],
        )

        self.assertEqual(restored, f"查看入口：[打开原始链接]({self.original})")

    def test_does_not_guess_when_same_path_has_multiple_original_links(self) -> None:
        another = self.original.replace("prometheus", "loki")
        answer = "[查看图表](http://localhost:3000/explore?changed=...&orgId=1)"

        restored = restore_truncated_tool_links(answer, [self.original, another])

        self.assertEqual(restored, answer)

    def test_keeps_complete_markdown_link_unchanged(self) -> None:
        answer = f"[查看图表]({self.original})"

        self.assertEqual(
            restore_truncated_tool_links(answer, [self.original]),
            answer,
        )


if __name__ == "__main__":
    unittest.main()
