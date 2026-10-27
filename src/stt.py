"""Zamiana nagrania na tekst (opcjonalna - wymaga `pip install faster-whisper`)."""

from functools import lru_cache

# Podpowiedź dla Whispera: poprawia rozpoznawanie słownictwa medycznego.
PODPOWIEDZ = "Dyktowanie lekarza: recepta, dawkowanie, miligramy, tabletki, amoksycylina, ibuprofen, rozpoznanie."


@lru_cache
def _whisper(rozmiar: str):
    from faster_whisper import WhisperModel

    return WhisperModel(rozmiar, compute_type="int8")


def transkrybuj(sciezka_audio: str, rozmiar: str = "large-v3") -> str:
    segmenty, _ = _whisper(rozmiar).transcribe(sciezka_audio, language="pl", initial_prompt=PODPOWIEDZ)
    return " ".join(s.text.strip() for s in segmenty)
