"""Tests for pure helper functions from the Telegram adapter and renderer."""
from __future__ import annotations

from afk.adapters.telegram.adapter import _split_message, MAX_MESSAGE_LENGTH
from afk.adapters.telegram.renderer import (
    _is_web_channel,
    _summarize_tool_args,
    _summarize_tool_result,
)


class TestSplitMessage:
    def test_short_message_no_split(self):
        assert _split_message("hello") == ["hello"]

    def test_exactly_max_length(self):
        text = "a" * MAX_MESSAGE_LENGTH
        assert _split_message(text) == [text]

    def test_split_at_newline(self):
        # Build a text just over 4096 that has a newline before the limit
        line = "x" * 2000
        text = f"{line}\n{line}\n{line}"
        chunks = _split_message(text)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= MAX_MESSAGE_LENGTH

    def test_split_no_newline_hard_cut(self):
        text = "a" * (MAX_MESSAGE_LENGTH + 100)
        chunks = _split_message(text)
        assert len(chunks) == 2
        assert len(chunks[0]) == MAX_MESSAGE_LENGTH
        assert len(chunks[1]) == 100

    def test_multiple_splits(self):
        text = "a" * (MAX_MESSAGE_LENGTH * 3)
        chunks = _split_message(text)
        assert len(chunks) == 3


class TestSummarizeToolArgs:
    def test_string_input(self):
        assert _summarize_tool_args("do the thing") == "do the thing"

    def test_string_truncated(self):
        long_str = "x" * 500
        result = _summarize_tool_args(long_str)
        assert len(result) == 300

    def test_dict_with_command(self):
        assert _summarize_tool_args({"command": "ls -la"}) == "ls -la"

    def test_dict_with_content(self):
        result = _summarize_tool_args({"content": "short"})
        assert result == "short"

    def test_dict_with_content_truncated(self):
        result = _summarize_tool_args({"content": "x" * 500})
        assert len(result) == 200

    def test_dict_with_file_path(self):
        assert _summarize_tool_args({"file_path": "/a/b.py"}) == "/a/b.py"

    def test_dict_fallback(self):
        result = _summarize_tool_args({"other": "val"})
        assert "other" in result

    def test_empty_dict(self):
        result = _summarize_tool_args({})
        assert isinstance(result, str)


class TestSummarizeToolResult:
    def test_normal_result(self):
        result = _summarize_tool_result({
            "type": "tool_result",
            "content": "output here",
            "is_error": False,
        })
        assert "Tool result" in result
        assert "output here" in result

    def test_error_result(self):
        result = _summarize_tool_result({
            "type": "tool_result",
            "content": "failed",
            "is_error": True,
        })
        assert "Tool error" in result

    def test_empty_content(self):
        result = _summarize_tool_result({
            "type": "tool_result",
            "content": "",
        })
        assert result == ""

    def test_none_content(self):
        result = _summarize_tool_result({
            "type": "tool_result",
        })
        assert result == ""

    def test_long_content_truncated(self):
        result = _summarize_tool_result({
            "type": "tool_result",
            "content": "x" * 600,
            "is_error": False,
        })
        # Should be truncated around 500 chars + prefix
        assert len(result) < 600

    def test_list_content(self):
        result = _summarize_tool_result({
            "type": "tool_result",
            "content": [
                {"type": "text", "text": "line1"},
                {"type": "text", "text": "line2"},
            ],
            "is_error": False,
        })
        assert "line1" in result
        assert "line2" in result


class TestIsWebChannel:
    def test_web_prefix(self):
        assert _is_web_channel("web:abc123") is True

    def test_non_web(self):
        assert _is_web_channel("12345") is False

    def test_empty(self):
        assert _is_web_channel("") is False
