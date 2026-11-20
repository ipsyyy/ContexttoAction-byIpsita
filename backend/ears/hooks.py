import re
from memory.belief import on_partial_utterance

NOISE = {"", "uh", "um", "uhh", "hmm", "hm", "yeah", "okay", "ok",
         "right", "sure", "yep", "nope", "no", "yes"}

# Transcriptions that are purely ElevenLabs sound-effect annotations
_PAREN_ONLY = re.compile(r'^(\s*\([^)]*\)\s*)+$')
# Trailing paren annotations appended to real speech, e.g. "7,000 (music plays)"
_TRAILING_PAREN = re.compile(r'(\s*\([^)]*\))+\s*$')

_paused = False


def set_paused(paused: bool) -> None:
    global _paused
    _paused = paused


def on_utterance_final(text: str) -> None:
    if _paused:
        return
    t = text.strip()
    if t.lower() in NOISE:
        return
    # Skip pure sound-effect annotations from ElevenLabs
    if _PAREN_ONLY.match(t):
        return
    # Strip trailing sound-effect annotations before passing downstream
    t = _TRAILING_PAREN.sub('', t).strip()
    # Skip very short clips (< 4 words) that are likely noise
    if len(t.split()) < 4:
        return
    from decision.loop import on_utterance_final as _decision
    _decision(t)
