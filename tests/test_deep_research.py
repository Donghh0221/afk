"""Tests for DeepResearchAgent helper methods."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from afk.adapters.experimental.deep_research.agent import DeepResearchAgent


class TestExtractCitations:
    def test_basic_extraction(self):
        response = SimpleNamespace(output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(annotations=[
                        SimpleNamespace(
                            type="url_citation",
                            title="Example",
                            url="https://example.com",
                        ),
                    ]),
                ],
            ),
        ])
        citations = DeepResearchAgent._extract_citations(response)
        assert len(citations) == 1
        assert citations[0]["title"] == "Example"
        assert citations[0]["url"] == "https://example.com"

    def test_skips_non_message_items(self):
        response = SimpleNamespace(output=[
            SimpleNamespace(type="web_search", content=[]),
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(annotations=[
                        SimpleNamespace(
                            type="url_citation",
                            title="Only",
                            url="https://only.com",
                        ),
                    ]),
                ],
            ),
        ])
        citations = DeepResearchAgent._extract_citations(response)
        assert len(citations) == 1
        assert citations[0]["url"] == "https://only.com"

    def test_no_annotations(self):
        response = SimpleNamespace(output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(annotations=[]),
                ],
            ),
        ])
        citations = DeepResearchAgent._extract_citations(response)
        assert citations == []

    def test_non_url_citation_annotations_skipped(self):
        response = SimpleNamespace(output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(annotations=[
                        SimpleNamespace(type="file_citation", title="X"),
                    ]),
                ],
            ),
        ])
        citations = DeepResearchAgent._extract_citations(response)
        assert citations == []

    def test_empty_output(self):
        response = SimpleNamespace(output=[])
        citations = DeepResearchAgent._extract_citations(response)
        assert citations == []

    def test_multiple_citations_across_blocks(self):
        response = SimpleNamespace(output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(annotations=[
                        SimpleNamespace(type="url_citation", title="A", url="https://a.com"),
                        SimpleNamespace(type="url_citation", title="B", url="https://b.com"),
                    ]),
                    SimpleNamespace(annotations=[
                        SimpleNamespace(type="url_citation", title="C", url="https://c.com"),
                    ]),
                ],
            ),
        ])
        citations = DeepResearchAgent._extract_citations(response)
        assert len(citations) == 3


class TestSaveReport:
    def test_save_to_root(self, tmp_path: Path):
        agent = DeepResearchAgent.__new__(DeepResearchAgent)
        agent._working_dir = str(tmp_path)

        path = agent._save_report("# Report\nHello")
        assert path == tmp_path / "report.md"
        assert path.read_text() == "# Report\nHello"

    def test_save_to_output_dir(self, tmp_path: Path):
        (tmp_path / "output").mkdir()
        agent = DeepResearchAgent.__new__(DeepResearchAgent)
        agent._working_dir = str(tmp_path)

        path = agent._save_report("# Report")
        assert path == tmp_path / "output" / "report.md"
        assert path.read_text() == "# Report"
