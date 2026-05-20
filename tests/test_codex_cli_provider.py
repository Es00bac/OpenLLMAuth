import asyncio

import pytest

from open_llm_auth.providers.codex_cli import CodexCliProvider


def test_parse_cli_output_prefers_completed_agent_message_text():
    provider = CodexCliProvider()
    output = "\n".join(
        [
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"gpt-5.4"}}',
            '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}',
        ]
    )

    response = provider._parse_cli_output(output, "codex-cli/gpt-5.4")

    assert response["choices"][0]["message"]["content"] == "gpt-5.4"


def test_parse_cli_output_stringifies_non_string_result_payload():
    provider = CodexCliProvider()
    output = '{"result":{"ok":true,"answer":"done"}}'

    response = provider._parse_cli_output(output, "codex-cli/gpt-5.4")

    assert response["choices"][0]["message"]["content"] == '{"ok": true, "answer": "done"}'


def test_parse_cli_output_converts_bridge_json_to_tool_calls():
    provider = CodexCliProvider()
    output = '{"type":"agent_message","text":"{\\"tool_calls\\":[{\\"name\\":\\"web_search\\",\\"arguments\\":{\\"query\\":\\"5ch current topic\\"}}]}"}'

    response = provider._parse_cli_output(
        output,
        "codex-cli/gpt-5.5",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    message = response["choices"][0]["message"]
    assert response["choices"][0]["finish_reason"] == "tool_calls"
    assert message["tool_calls"][0]["function"]["name"] == "web_search"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"query": "5ch current topic"}'


def test_parse_cli_output_converts_bridge_final_to_content():
    provider = CodexCliProvider()
    output = '{"type":"agent_message","text":"{\\"final\\":\\"done\\"}"}'

    response = provider._parse_cli_output(
        output,
        "codex-cli/gpt-5.5",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    assert response["choices"][0]["message"]["content"] == "done"
    assert "tool_calls" not in response["choices"][0]["message"]


def test_build_cli_args_includes_reasoning_effort_override():
    provider = CodexCliProvider(cli_path="codex")

    args = provider._build_cli_args(
        "gpt-5.4",
        [{"role": "user", "content": "hello"}],
        reasoning_effort="xhigh",
    )

    assert args[:5] == ["codex", "--model", "gpt-5.4", "-c", 'model_reasoning_effort="xhigh"']


def test_build_cli_args_defaults_to_workspace_write_sandbox():
    provider = CodexCliProvider(cli_path="codex")

    args = provider._build_cli_args(
        "gpt-5.4",
        [{"role": "user", "content": "save the file"}],
    )

    sandbox_index = args.index("--sandbox")
    assert args[sandbox_index + 1] == "workspace-write"


def test_build_cli_args_can_use_stdin_prompt_placeholder():
    provider = CodexCliProvider(cli_path="codex")

    args = provider._build_cli_args(
        "gpt-5.4",
        [{"role": "user", "content": "ignored"}],
        prompt="x" * 70_000,
        pipe_prompt=True,
    )

    assert args[-1] == "-"


@pytest.mark.asyncio
async def test_chat_completion_pipes_prompt_to_stdin(monkeypatch: pytest.MonkeyPatch):
    provider = CodexCliProvider(cli_path="codex")
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, input=None):
            captured["input"] = input
            return (b'{"type":"agent_message","text":"ok"}\n', b"")

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await provider.chat_completion(
        model="gpt-5.4",
        messages=[{"role": "user", "content": "this prompt is long"}],
        payload={},
    )

    assert response["choices"][0]["message"]["content"] == "ok"
    assert captured["args"][-1] == "-"
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE
    assert captured["input"] == b"this prompt is long"


@pytest.mark.asyncio
async def test_chat_completion_injects_tool_bridge(monkeypatch: pytest.MonkeyPatch):
    provider = CodexCliProvider(cli_path="codex")
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, input=None):
            captured["input"] = input.decode("utf-8")
            return (b'{"type":"agent_message","text":"{\\"final\\":\\"ok\\"}"}\n', b"")

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await provider.chat_completion(
        model="gpt-5.5",
        messages=[{"role": "user", "content": "search this"}],
        payload={
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web",
                        "parameters": {"type": "object"},
                    },
                }
            ]
        },
    )

    assert response["choices"][0]["message"]["content"] == "ok"
    assert "OpenCAS tool-call bridge" in captured["input"]
    assert "web_search" in captured["input"]


def test_codex_cli_provider_reports_reasoning_support():
    provider = CodexCliProvider()

    assert provider.supports_reasoning_effort(model="gpt-5.4") is True
