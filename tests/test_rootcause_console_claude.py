"""Claude stream-JSON normalization for the root-cause console."""

from scripts.analysis.rootcause_console import normalize_trace


def test_normalizes_claude_stream_json_with_tool_results_and_model_usage() -> None:
    records = [
        {
            "type": "system",
            "subtype": "init",
            "model": "claude-sonnet-5",
            "claude_code_version": "2.1.217",
            "tools": ["Bash", "Write", "mcp__sourcegraph__keyword_search"],
            "mcp_servers": [{"name": "sourcegraph", "status": "connected"}],
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "Trace the dependency."},
                    {"type": "text", "text": "I found the relevant module."},
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "mcp__sourcegraph__keyword_search",
                        "input": {"query": "golang.org/x/text"},
                    },
                    {
                        "type": "tool_use",
                        "id": "tool-2",
                        "name": "Write",
                        "input": {
                            "file_path": "/workspace/agent_output/answer.json",
                            "content": "{}",
                        },
                    },
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "go.mod:68",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-2",
                        "content": "wrote file",
                    },
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 4,
            "total_cost_usd": 0.25,
            "modelUsage": {
                "claude-sonnet-5": {
                    "inputTokens": 10,
                    "outputTokens": 20,
                    "costUSD": 0.25,
                }
            },
        },
    ]

    trace, activity, writes = normalize_trace(records)

    assert activity["provider"] == "claude"
    assert activity["primary_unit"] == "turn"
    assert activity["primary_count"] == 4
    assert activity["label"] == "4 Claude turns"
    assert activity["tool_uses"] == 2
    assert activity["agent_messages"] == 1
    assert activity["file_changes"] == 1
    assert activity["model_usage"]["claude-sonnet-5"]["costUSD"] == 0.25
    finder = next(
        event
        for event in trace
        if event.get("name") == "sourcegraph.keyword_search"
    )
    assert finder["status"] == "ok"
    assert finder["result"] == "go.mod:68"
    assert any(event["kind"] == "thinking" for event in trace)
    assert any(event["kind"] == "boundary" for event in trace)
    assert writes == [
        {
            "path": "/workspace/agent_output/answer.json",
            "status": "ok",
            "content": "{}",
        }
    ]


def test_claude_failed_write_is_not_reported_as_written() -> None:
    records = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "write-1",
                        "name": "Write",
                        "input": {
                            "file_path": "/workspace/agent_output/answer.json",
                            "content": "{}",
                        },
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "write-1",
                        "is_error": True,
                        "content": "permission denied",
                    }
                ]
            },
        },
    ]

    trace, _, writes = normalize_trace(records)

    assert trace[0]["status"] == "error"
    assert writes[0]["status"] == "error"
