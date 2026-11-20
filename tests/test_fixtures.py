from decision.fixtures import DECOYS, TRIGGERS


def test_decoys_nonempty():
    assert len(DECOYS) >= 10


def test_triggers_nonempty():
    assert len(TRIGGERS) >= 1


def test_trigger_format():
    for utterance, expected_whisper in TRIGGERS:
        assert isinstance(utterance, str) and utterance.strip()
        assert isinstance(expected_whisper, str) and expected_whisper.strip()


def test_trigger_not_in_decoys():
    trigger_utterances = {u for u, _ in TRIGGERS}
    for decoy in DECOYS:
        assert decoy not in trigger_utterances, f"Decoy is also a trigger: {decoy!r}"
