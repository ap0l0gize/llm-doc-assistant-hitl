"""Dopasowanie podyktowanej nazwy leku do bazy leków.

Robi to zwykły kod, nie model - na recepcie nie pojawi się nic spoza bazy.
data/leki.csv to mała próbka do testów; docelowo podmienić na eksport
z Rejestru Produktów Leczniczych (te same kolumny).
"""

import csv
import re
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz

LEKI_CSV = Path(__file__).parent.parent / "data" / "leki.csv"
PROG_PODOBIENSTWA = 80


@lru_cache
def _baza() -> list[dict]:
    with open(LEKI_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _liczby(tekst: str | None) -> set[str]:
    return set(re.findall(r"\d+", tekst or ""))


def dopasuj(nazwa: str, dawka: str | None = None, limit: int = 3) -> list[dict]:
    """Zwraca do `limit` kandydatów z bazy, najlepsi pierwsi."""
    szukana = nazwa.lower()
    kandydaci = []
    for lek in _baza():
        wynik = max(
            fuzz.WRatio(szukana, lek["nazwa"].lower()),
            fuzz.WRatio(szukana, lek["substancja"].lower()),
        )
        if wynik < PROG_PODOBIENSTWA:
            continue
        zgodna_moc = bool(dawka) and bool(_liczby(dawka) & _liczby(lek["moc"]))
        kandydaci.append({**lek, "podobienstwo": round(wynik), "zgodna_moc": zgodna_moc})
    kandydaci.sort(key=lambda k: (k["zgodna_moc"], k["podobienstwo"]), reverse=True)
    return kandydaci[:limit]
