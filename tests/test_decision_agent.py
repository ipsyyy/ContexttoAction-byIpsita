from unittest.mock import patch, MagicMock
import decision.agent as agent_module


def _mock_response(json_text: str):
    block = MagicMock()
    block.text = json_text
    response = MagicMock()
    response.content = [block]
    return response


def test_returns_silent_on_decoy():
    with patch.object(agent_module.client.messages, "create",
                      return_value=_mock_response('{"action": "silent"}')):
        with patch("decision.agent.get_latest_facts", return_value=["fact"]):
            result = agent_module.make_decision("Let me think about that.")
    assert result == {"action": "silent"}


def test_returns_whisper_on_trigger():
    whisper = '{"action": "whisper", "content": "He said 18 months on April call. He just said 12."}'
    with patch.object(agent_module.client.messages, "create",
                      return_value=_mock_response(whisper)):
        with patch("decision.agent.get_latest_facts", return_value=["April 15 call: Marcus stated 18 months runway."]):
            result = agent_module.make_decision("We're tracking about 12 months of runway right now.")
    assert result["action"] == "whisper"
    assert "content" in result


def test_returns_silent_on_api_error():
    with patch.object(agent_module.client.messages, "create", side_effect=Exception("timeout")):
        with patch("decision.agent.get_latest_facts", return_value=[]):
            result = agent_module.make_decision("anything")
    assert result == {"action": "silent"}


def test_returns_silent_on_malformed_json():
    with patch.object(agent_module.client.messages, "create",
                      return_value=_mock_response("not json")):
        with patch("decision.agent.get_latest_facts", return_value=[]):
            result = agent_module.make_decision("anything")
    assert result == {"action": "silent"}
