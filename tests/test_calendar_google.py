"""Tests for backend/action/calendar_google.py and its wiring in agent.py.

Coverage:
- token.json absent: is_available False; check / block return None; the
  action agent falls back to today's stub print + card-only behavior.
- Service available (mocked): check_availability returns parsed events;
  block returns event_id; both surface on the action card.
- Date parser tolerates 'YYYY-MM-DD' (full-day window) and full ISO 8601
  (±2-hour window).
- API error / timeout: sentinel None, never raises.
- _execute_auto integration: when available, real path drives the card
  payload; when not, behavior matches today exactly.
"""

from unittest.mock import MagicMock, patch
import datetime

import pytest

from action import calendar_google
from action import agent as action_agent
from action import hangup


@pytest.fixture(autouse=True)
def _reset():
    calendar_google.reset_service()
    hangup.reset()
    yield
    calendar_google.reset_service()
    hangup.reset()


def _mock_service(events_resp=None, insert_resp=None,
                  raise_on_list=False, raise_on_insert=False):
    """Build a Calendar v3 mock with the methods the facade calls."""
    service = MagicMock()
    if events_resp is None:
        events_resp = {"items": []}
    if insert_resp is None:
        insert_resp = {"id": "evt_test"}

    if raise_on_list:
        service.events.return_value.list.return_value.execute.side_effect = RuntimeError("boom")
    else:
        service.events.return_value.list.return_value.execute.return_value = events_resp
    if raise_on_insert:
        service.events.return_value.insert.return_value.execute.side_effect = RuntimeError("boom")
    else:
        service.events.return_value.insert.return_value.execute.return_value = insert_resp
    return service


# ── Absent-token path: stub fallback ────────────────────────────────────────


def test_is_available_false_when_no_token(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_google, "_TOKEN_FILE", str(tmp_path / "missing.json"))
    assert calendar_google.is_available() is False


def test_check_returns_none_without_service(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_google, "_TOKEN_FILE", str(tmp_path / "missing.json"))
    assert calendar_google.check_availability("2026-05-18") is None


def test_block_returns_none_without_service(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_google, "_TOKEN_FILE", str(tmp_path / "missing.json"))
    assert calendar_google.block("X", "2026-05-18T14:00:00", 30) is None


def test_execute_auto_check_calendar_falls_back_to_stub(capsys):
    """When calendar_google is unavailable, _execute_auto's behavior MUST
    match today exactly: stub log + card emission + hangup record/execute."""
    with patch.object(calendar_google, "is_available", return_value=False), \
         patch("action.agent.emit_action_card") as mock_emit:
        action_agent._execute_auto("check_calendar", {"date": "2026-05-18"})

    out = capsys.readouterr().out
    assert "(stub) no events" in out
    mock_emit.assert_called_once()
    # Card inputs unchanged (no `events` field injected) when in stub mode.
    name, inputs = mock_emit.call_args.args[0], mock_emit.call_args.args[1]
    assert name == "check_calendar"
    assert "events" not in inputs
    snap = hangup.snapshot()
    assert snap[0]["state"] == hangup.EXECUTED


def test_execute_auto_block_calendar_falls_back_to_stub(capsys):
    with patch.object(calendar_google, "is_available", return_value=False), \
         patch("action.agent.emit_action_card") as mock_emit:
        action_agent._execute_auto(
            "block_calendar",
            {"title": "Sync", "date": "2026-05-18T14:00:00", "duration_minutes": 30},
        )
    out = capsys.readouterr().out
    assert "(stub) ok" in out
    mock_emit.assert_called_once()
    _, inputs = mock_emit.call_args.args[0], mock_emit.call_args.args[1]
    assert "event_id" not in inputs


# ── Present service: real path (mocked) ─────────────────────────────────────


def test_check_returns_events_when_service_available():
    service = _mock_service(events_resp={
        "items": [
            {"summary": "Sync w/ Ben", "start": {"dateTime": "2026-05-18T14:00:00Z"},
             "end": {"dateTime": "2026-05-18T14:30:00Z"}},
            {"summary": "Lunch", "start": {"date": "2026-05-18"}, "end": {"date": "2026-05-19"}},
        ]
    })
    with patch.object(calendar_google, "_get_service", return_value=service):
        events = calendar_google.check_availability("2026-05-18")

    assert events is not None
    assert len(events) == 2
    assert events[0]["summary"] == "Sync w/ Ben"
    assert events[0]["start"] == "2026-05-18T14:00:00Z"
    # Date-only event normalized.
    assert events[1]["start"] == "2026-05-18"


def test_check_full_day_window_for_date_only_input():
    service = _mock_service()
    with patch.object(calendar_google, "_get_service", return_value=service):
        calendar_google.check_availability("2026-05-18")
    # The mock recorded the kwargs passed to events().list(...) — assert the
    # window matches a full UTC day.
    call_kwargs = service.events.return_value.list.call_args.kwargs
    assert call_kwargs["timeMin"].startswith("2026-05-18T00:00:00")
    assert call_kwargs["timeMax"].startswith("2026-05-19T00:00:00")


def test_check_pm_window_for_full_iso_input():
    service = _mock_service()
    with patch.object(calendar_google, "_get_service", return_value=service):
        calendar_google.check_availability("2026-05-18T14:00:00")
    call_kwargs = service.events.return_value.list.call_args.kwargs
    # ±2 hour window around 14:00.
    assert call_kwargs["timeMin"].startswith("2026-05-18T12:00:00")
    assert call_kwargs["timeMax"].startswith("2026-05-18T16:00:00")


def test_block_returns_event_id():
    service = _mock_service(insert_resp={"id": "evt_xyz"})
    with patch.object(calendar_google, "_get_service", return_value=service):
        event_id = calendar_google.block("Hold", "2026-05-18T14:00:00", 45)
    assert event_id == "evt_xyz"
    call_args = service.events.return_value.insert.call_args
    body = call_args.kwargs["body"]
    assert body["summary"] == "Hold"
    assert body["start"]["dateTime"] == "2026-05-18T14:00:00"
    # 14:00 + 45min = 14:45
    assert body["end"]["dateTime"] == "2026-05-18T14:45:00"


def test_block_defaults_bad_duration_to_60():
    service = _mock_service()
    with patch.object(calendar_google, "_get_service", return_value=service):
        calendar_google.block("Hold", "2026-05-18T14:00:00", "not-an-int")
    body = service.events.return_value.insert.call_args.kwargs["body"]
    assert body["end"]["dateTime"] == "2026-05-18T15:00:00"


# ── Failure paths: sentinel + no raise ──────────────────────────────────────


def test_check_sdk_error_returns_none():
    service = _mock_service(raise_on_list=True)
    with patch.object(calendar_google, "_get_service", return_value=service):
        assert calendar_google.check_availability("2026-05-18") is None


def test_block_sdk_error_returns_none():
    service = _mock_service(raise_on_insert=True)
    with patch.object(calendar_google, "_get_service", return_value=service):
        assert calendar_google.block("X", "2026-05-18T14:00:00", 30) is None


def test_invalid_date_returns_none():
    service = _mock_service()
    with patch.object(calendar_google, "_get_service", return_value=service):
        assert calendar_google.check_availability("not-a-date") is None
        assert calendar_google.block("X", "garbage", 30) is None
    # No SDK call attempted on bad date.
    service.events.return_value.list.return_value.execute.assert_not_called()
    service.events.return_value.insert.return_value.execute.assert_not_called()


# ── _execute_auto integration when service is available ────────────────────


def test_execute_auto_check_calendar_surfaces_real_events(capsys):
    with patch.object(calendar_google, "is_available", return_value=True), \
         patch.object(calendar_google, "check_availability",
                      return_value=[{"summary": "Sync", "start": "...", "end": "..."}]), \
         patch("action.agent.emit_action_card") as mock_emit:
        action_agent._execute_auto("check_calendar", {"date": "2026-05-18"})

    name, inputs = mock_emit.call_args.args[0], mock_emit.call_args.args[1]
    assert name == "check_calendar"
    assert inputs.get("events") == [{"summary": "Sync", "start": "...", "end": "..."}]
    out = capsys.readouterr().out
    assert "Sync" in out


def test_execute_auto_block_calendar_surfaces_event_id():
    with patch.object(calendar_google, "is_available", return_value=True), \
         patch.object(calendar_google, "block", return_value="evt_abc"), \
         patch("action.agent.emit_action_card") as mock_emit:
        action_agent._execute_auto(
            "block_calendar",
            {"title": "Hold", "date": "2026-05-18T14:00:00", "duration_minutes": 30},
        )
    _, inputs = mock_emit.call_args.args[0], mock_emit.call_args.args[1]
    assert inputs.get("event_id") == "evt_abc"


def test_execute_auto_block_real_failure_falls_back_to_stub(capsys):
    """When the real path returns None (timeout/error), the card still
    fires with the original inputs and the stub message is logged."""
    with patch.object(calendar_google, "is_available", return_value=True), \
         patch.object(calendar_google, "block", return_value=None), \
         patch("action.agent.emit_action_card") as mock_emit:
        action_agent._execute_auto(
            "block_calendar",
            {"title": "Hold", "date": "2026-05-18T14:00:00", "duration_minutes": 30},
        )
    out = capsys.readouterr().out
    assert "(stub) ok" in out
    _, inputs = mock_emit.call_args.args[0], mock_emit.call_args.args[1]
    assert "event_id" not in inputs
