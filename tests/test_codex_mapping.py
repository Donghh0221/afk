"""Tests for Codex item-to-content_blocks mapping."""
from __future__ import annotations

from afk.adapters.experimental.codex.agent import _map_item_to_content_blocks


class TestMapItemToContentBlocks:
    def test_agent_message(self):
        blocks = _map_item_to_content_blocks({
            "type": "agent_message",
            "text": "Hello world",
        })
        assert len(blocks) == 1
        assert blocks[0] == {"type": "text", "text": "Hello world"}

    def test_agent_message_empty_text(self):
        blocks = _map_item_to_content_blocks({
            "type": "agent_message",
            "text": "",
        })
        assert blocks == []

    def test_reasoning(self):
        blocks = _map_item_to_content_blocks({
            "type": "reasoning",
            "text": "thinking...",
        })
        assert len(blocks) == 1
        assert blocks[0]["text"] == "[reasoning] thinking..."

    def test_command_execution(self):
        blocks = _map_item_to_content_blocks({
            "type": "command_execution",
            "command": "ls -la",
            "aggregated_output": "file1\nfile2",
            "exit_code": 0,
        })
        assert len(blocks) == 2
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["name"] == "Bash"
        assert blocks[0]["input"]["command"] == "ls -la"
        assert blocks[1]["type"] == "tool_result"
        assert blocks[1]["is_error"] is False

    def test_command_execution_nonzero_exit(self):
        blocks = _map_item_to_content_blocks({
            "type": "command_execution",
            "command": "false",
            "aggregated_output": "",
            "exit_code": 1,
        })
        assert blocks[1]["is_error"] is True

    def test_file_change(self):
        blocks = _map_item_to_content_blocks({
            "type": "file_change",
            "changes": [
                {"path": "a.py", "change_kind": "create"},
                {"path": "b.py", "change_kind": "modify"},
            ],
        })
        assert len(blocks) == 1
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["name"] == "FileChange"
        assert "create: a.py" in blocks[0]["input"]["changes"]
        assert "modify: b.py" in blocks[0]["input"]["changes"]

    def test_mcp_tool_call(self):
        blocks = _map_item_to_content_blocks({
            "type": "mcp_tool_call",
            "tool_name": "search",
            "arguments": {"q": "test"},
            "content": "results here",
        })
        assert len(blocks) == 2
        assert blocks[0]["name"] == "MCP:search"
        assert blocks[1]["type"] == "tool_result"
        assert blocks[1]["content"] == "results here"

    def test_mcp_tool_call_no_content(self):
        blocks = _map_item_to_content_blocks({
            "type": "mcp_tool_call",
            "tool_name": "ping",
            "arguments": {},
        })
        assert len(blocks) == 1
        assert blocks[0]["type"] == "tool_use"

    def test_web_search(self):
        blocks = _map_item_to_content_blocks({
            "type": "web_search",
            "query": "python asyncio",
        })
        assert len(blocks) == 1
        assert blocks[0]["name"] == "WebSearch"
        assert blocks[0]["input"]["query"] == "python asyncio"

    def test_error(self):
        blocks = _map_item_to_content_blocks({
            "type": "error",
            "text": "something broke",
        })
        assert len(blocks) == 1
        assert "Error:" in blocks[0]["text"]

    def test_error_with_message_key(self):
        blocks = _map_item_to_content_blocks({
            "type": "error",
            "message": "from message key",
        })
        assert "from message key" in blocks[0]["text"]

    def test_unsupported_type(self):
        blocks = _map_item_to_content_blocks({
            "type": "unknown_future_type",
            "data": 123,
        })
        assert blocks == []

    def test_missing_type(self):
        blocks = _map_item_to_content_blocks({})
        assert blocks == []
