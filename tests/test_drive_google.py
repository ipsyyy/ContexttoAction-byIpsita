"""Tests for backend/action/drive_google.py and its wiring into pull_document.

Coverage:
- token.json absent: is_available False, search returns None, action agent
  falls back to today's stub print + card-only behavior.
- Service available (mocked): search parses files into the documented shape.
- Empty search ('') returns None without an SDK call.
- API error / timeout: sentinel None, never raises.
- _execute_auto integration: matches surface on the action card when
  available; stub log preserved when not.
"""

from unittest.mock import MagicMock, patch
import pytest

from action import drive_google
from action import agent as action_agent
from action import hangup


@pytest.fixture(autouse=True)
def _reset():
    drive_google.reset_service()
    hangup.reset()
    yield
    drive_google.reset_service()
    hangup.reset()


def _mock_service(files=None, raise_on_list=False):
    service = MagicMock()
    if files is None:
        files = []
    if raise_on_list:
        service.files.return_value.list.return_value.execute.side_effect = RuntimeError("boom")
    else:
        service.files.return_value.list.return_value.execute.return_value = {"files": files}
    return service


# ── Absent-token: stub fallback ─────────────────────────────────────────────


def test_is_available_false_when_no_token(tmp_path, monkeypatch):
    monkeypatch.setattr(drive_google, "_TOKEN_FILE", str(tmp_path / "missing.json"))
    assert drive_google.is_available() is False


def test_search_returns_none_without_service(tmp_path, monkeypatch):
    monkeypatch.setattr(drive_google, "_TOKEN_FILE", str(tmp_path / "missing.json"))
    assert drive_google.search("Q2 deck") is None


def test_execute_auto_pull_document_falls_back_to_stub(capsys):
    """When drive_google is unavailable, _execute_auto's behavior MUST match
    today exactly: stub log + card emission + hangup record/execute."""
    with patch.object(drive_google, "is_available", return_value=False), \
         patch("action.agent.emit_action_card") as mock_emit:
        action_agent._execute_auto("pull_document", {"description": "Q2 board deck"})

    out = capsys.readouterr().out
    assert "(stub) found 1 match" in out
    mock_emit.assert_called_once()
    _, inputs = mock_emit.call_args.args[0], mock_emit.call_args.args[1]
    assert "matches" not in inputs
    assert hangup.snapshot()[0]["state"] == hangup.EXECUTED


# ── Present service: real path (mocked) ─────────────────────────────────────


def test_search_returns_parsed_files():
    service = _mock_service(files=[
        {
            "id": "1abc",
            "name": "Q2 board deck",
            "mimeType": "application/vnd.google-apps.presentation",
            "webViewLink": "https://docs.google.com/presentation/d/1abc",
            "modifiedTime": "2026-05-10T12:00:00Z",
        },
        {
            "id": "2def",
            "name": "Old Q2 notes",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://docs.google.com/document/d/2def",
            "modifiedTime": "2026-04-15T09:00:00Z",
        },
    ])
    with patch.object(drive_google, "_get_service", return_value=service):
        out = drive_google.search("Q2 deck")

    assert out is not None
    assert len(out) == 2
    assert out[0]["name"] == "Q2 board deck"
    assert out[0]["webViewLink"].endswith("1abc")
    assert out[1]["id"] == "2def"


def test_search_query_assembles_correctly():
    """Verify the Drive q= string contains both fullText and name predicates
    and the trashed filter."""
    service = _mock_service()
    with patch.object(drive_google, "_get_service", return_value=service):
        drive_google.search("runway 18 months")
    call_kwargs = service.files.return_value.list.call_args.kwargs
    q = call_kwargs["q"]
    assert "fullText contains 'runway 18 months'" in q
    assert "name contains 'runway 18 months'" in q
    assert "trashed = false" in q
    assert call_kwargs["pageSize"] == drive_google.SEARCH_RESULT_LIMIT


def test_search_escapes_single_quotes():
    """Single quotes in the query must be backslash-escaped or the Drive
    query parser will choke. Defensive."""
    service = _mock_service()
    with patch.object(drive_google, "_get_service", return_value=service):
        drive_google.search("Marcus's runway memo")
    q = service.files.return_value.list.call_args.kwargs["q"]
    # Either escaped or the apostrophe is gone — the call must not have
    # injected a raw unescaped quote that breaks q parsing.
    assert "Marcus\\'s" in q or "Marcuss" in q or "Marcus s" in q


def test_search_empty_query_returns_none_without_sdk_call():
    service = _mock_service()
    with patch.object(drive_google, "_get_service", return_value=service):
        assert drive_google.search("") is None
        assert drive_google.search("   ") is None
        assert drive_google.search(None) is None
    service.files.return_value.list.return_value.execute.assert_not_called()


def test_search_empty_result_returns_empty_list():
    service = _mock_service(files=[])
    with patch.object(drive_google, "_get_service", return_value=service):
        out = drive_google.search("nothing matches this")
    assert out == []


# ── Failure paths: sentinel + no raise ──────────────────────────────────────


def test_search_sdk_error_returns_none():
    service = _mock_service(raise_on_list=True)
    with patch.object(drive_google, "_get_service", return_value=service):
        assert drive_google.search("Q2 deck") is None


# ── _execute_auto integration when service is available ────────────────────


def test_execute_auto_pull_document_surfaces_matches():
    fake_matches = [{
        "id": "1abc",
        "name": "Q2 board deck",
        "mimeType": "application/vnd.google-apps.presentation",
        "webViewLink": "https://docs.google.com/presentation/d/1abc",
        "modifiedTime": "2026-05-10T12:00:00Z",
    }]
    with patch.object(drive_google, "is_available", return_value=True), \
         patch.object(drive_google, "search", return_value=fake_matches), \
         patch("action.agent.emit_action_card") as mock_emit:
        action_agent._execute_auto("pull_document", {"description": "Q2 board deck"})

    name, inputs = mock_emit.call_args.args[0], mock_emit.call_args.args[1]
    assert name == "pull_document"
    assert inputs.get("matches") == fake_matches


def test_execute_auto_pull_document_empty_result_surfaces_empty_list(capsys):
    with patch.object(drive_google, "is_available", return_value=True), \
         patch.object(drive_google, "search", return_value=[]), \
         patch("action.agent.emit_action_card") as mock_emit:
        action_agent._execute_auto("pull_document", {"description": "no matches"})

    out = capsys.readouterr().out
    assert "no matches in Drive" in out
    _, inputs = mock_emit.call_args.args[0], mock_emit.call_args.args[1]
    assert inputs.get("matches") == []


def test_execute_auto_pull_document_search_failure_preserves_stub(capsys):
    with patch.object(drive_google, "is_available", return_value=True), \
         patch.object(drive_google, "search", return_value=None), \
         patch("action.agent.emit_action_card") as mock_emit:
        action_agent._execute_auto("pull_document", {"description": "Q2 board deck"})
    out = capsys.readouterr().out
    assert "(stub) found 1 match" in out
    _, inputs = mock_emit.call_args.args[0], mock_emit.call_args.args[1]
    assert "matches" not in inputs
