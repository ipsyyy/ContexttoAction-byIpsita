from unittest.mock import patch

from evaluation import telemetry


def test_disabled_trace_stays_local(monkeypatch):
    monkeypatch.delenv("CONTEXT_AGENT_TRACE_ENABLED", raising=False)
    with patch.object(telemetry._executor, "submit") as submit:
        assert telemetry.record("chat", mode="local", count=3) is None
        submit.assert_not_called()


def test_trace_allowlist_drops_private_data_and_unrecognized_values(monkeypatch):
    monkeypatch.setenv("CONTEXT_AGENT_TRACE_ENABLED", "1")
    monkeypatch.setenv("LANGSMITH_API_KEY", "synthetic-key")
    with patch.object(telemetry, "_pending"), patch.object(telemetry._executor, "submit") as submit:
        first = telemetry.record("chat", event_id="example", mode="local", count=3,
                                 transcript="private transcript", question="private question")
        second = telemetry.record("chat", event_id="example", mode="private data", count=2)
    assert first == second
    assert submit.call_args_list[0].args[-1] == {"mode": "local", "count": 3}
    assert submit.call_args_list[1].args[-1] == {"count": 2}
