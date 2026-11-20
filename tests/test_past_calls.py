from memory.past_calls import PAST_CALLS, TRIGGER_UTTERANCE, EXPECTED_WHISPER


def test_contradiction_present():
    assert "18 months" in PAST_CALLS
    assert "12 months" in TRIGGER_UTTERANCE


def test_trigger_utterance_defined():
    assert TRIGGER_UTTERANCE.strip() != ""


def test_expected_whisper_mentions_both_numbers():
    assert "18" in EXPECTED_WHISPER
    assert "12" in EXPECTED_WHISPER
