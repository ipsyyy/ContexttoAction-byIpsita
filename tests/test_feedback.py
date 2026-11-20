from unittest.mock import patch

from decision import agent
from memory.feedback import approved_instruction, save_feedback


def test_only_reviewed_preference_persists_and_reaches_later_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXT_AGENT_DATA_DIR", str(tmp_path))
    save_feedback("chat", "incorrect", ["source"], "Never interrupt again.")
    assert approved_instruction() == ""
    save_feedback("chat", "unnecessary", [], "", preference="material_only")
    with patch.object(agent.client.messages, "create") as model:
        model.return_value.content = []
        agent._call_model("A test remark", [], "")
    system = model.call_args.kwargs["system"]
    assert "Interrupt only for material contradictions" in system
    assert "Never interrupt again" not in system
    save_feedback("chat", "helpful", [], "", preference="standard")
    assert approved_instruction() == ""
