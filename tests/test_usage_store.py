import tempfile
from pathlib import Path

import pytest

from open_llm_auth.server.usage_store import UsageStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "usage.sqlite3"
        yield UsageStore(db_path=db)


def test_record_and_summary(store):
    store.record(provider="openai", model="gpt-4", endpoint="chat.completions", prompt_tokens=10, completion_tokens=5, latency_ms=100)
    store.record(provider="anthropic", model="claude-3", endpoint="chat.completions", prompt_tokens=20, completion_tokens=10, latency_ms=200)

    summary = store.get_summary(days=30)
    assert summary["requests"] == 2
    assert summary["total_tokens"] == 45
    assert summary["avg_latency_ms"] == 150.0


def test_chart_data(store):
    store.record(provider="openai", model="gpt-4", endpoint="chat.completions", prompt_tokens=10, completion_tokens=5, latency_ms=100)
    chart = store.get_chart_data(days=30)
    assert "labels" in chart
    assert "requests" in chart
    assert len(chart["labels"]) >= 1
    assert sum(chart["requests"]) == 1


def test_provider_breakdown(store):
    store.record(provider="openai", model="gpt-4", endpoint="chat.completions", prompt_tokens=10, completion_tokens=5, latency_ms=100)
    store.record(provider="openai", model="gpt-3.5", endpoint="chat.completions", prompt_tokens=5, completion_tokens=5, latency_ms=50)
    breakdown = store.get_provider_breakdown(days=30)
    assert len(breakdown) == 1
    assert breakdown[0]["provider"] == "openai"
    assert breakdown[0]["total_tokens"] == 25


def test_recent_records(store):
    store.record(provider="openai", model="gpt-4", endpoint="chat.completions", prompt_tokens=10, completion_tokens=5, latency_ms=100)
    records = store.get_recent_records(limit=10)
    assert len(records) == 1
    assert records[0]["provider"] == "openai"
