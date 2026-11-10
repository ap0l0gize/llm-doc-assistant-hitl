"""Speech-to-text (optional - requires `pip install faster-whisper`)."""

from functools import lru_cache

# Hint for Whisper (in Polish, like the dictation): improves recognition of medical vocabulary.
INITIAL_PROMPT = "Dyktowanie lekarza: recepta, dawkowanie, miligramy, tabletki, amoksycylina, ibuprofen, rozpoznanie."


@lru_cache
def _whisper(model_size: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, compute_type="int8")


def transcribe(audio_path: str, model_size: str = "large-v3") -> str:
    segments, _ = _whisper(model_size).transcribe(audio_path, language="pl", initial_prompt=INITIAL_PROMPT)
    return " ".join(s.text.strip() for s in segments)
