import decision.loop  # ensure module is imported before patching
from unittest.mock import patch
import ears.hooks as hooks_module


def test_noise_filtered():
    for noise in ["", "uh", "um", "uhh", "hmm", "hm", "UH", " um "]:
        with patch.object(decision.loop, "on_utterance_final") as mock:
            hooks_module.on_utterance_final(noise)
            mock.assert_not_called()


def test_real_utterance_passes_through():
    with patch.object(decision.loop, "on_utterance_final") as mock:
        hooks_module.on_utterance_final("We're tracking about 12 months of runway.")
        mock.assert_called_once_with("We're tracking about 12 months of runway.")
