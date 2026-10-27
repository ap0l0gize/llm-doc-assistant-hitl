"""Uruchomienie wizyty bez frontendu: lekarz "zatwierdza" przez edycję plików JSON.

    python run.py data/wizyty/wizyta_001.json          # interaktywnie
    python run.py data/wizyty/wizyta_001.json --auto   # zatwierdza drafty bez zmian

Przy każdym zatwierdzeniu draft trafia do out/<wizyta_id>/etapN_draft.json.
Popraw plik w edytorze i wciśnij Enter, żeby zatwierdzić, albo wpisz komentarz,
żeby odesłać dokumenty do agentów do poprawy.
"""

import argparse
import difflib
import json
import time
from pathlib import Path

from langgraph.types import Command

from src.graph import zbuduj_graf

OUT = Path(__file__).parent / "out"


def _zapisz(sciezka: Path, obj) -> None:
    sciezka.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _tekst_dyktowania(wizyta: dict, etap: int) -> str:
    if f"etap{etap}_dyktowanie" in wizyta:
        return wizyta[f"etap{etap}_dyktowanie"]
    from src.stt import transkrybuj

    return transkrybuj(wizyta[f"etap{etap}_audio"])


def _zatwierdzenie(zadanie: dict, katalog: Path, auto: bool, log: list) -> dict:
    etap = zadanie["etap"]
    plik = katalog / f"etap{etap}_draft.json"
    _zapisz(plik, zadanie["dokumenty"])
    print(f"\n=== Etap {etap}: draft do zatwierdzenia -> {plik}")
    for uwaga in zadanie["uwagi"]:
        print(f"  ! weryfikator [{uwaga['pole']}]: {uwaga['problem']}")

    start = time.time()
    komentarz = "" if auto else input("Enter = zatwierdź (z edycjami z pliku) | tekst = odeślij do poprawy: ").strip()
    wpis = {"etap": etap, "czas_przegladu_s": round(time.time() - start, 1)}
    if komentarz:
        log.append({**wpis, "decyzja": "do_poprawy", "komentarz": komentarz})
        return {"zatwierdzone": False, "komentarz": komentarz}

    zatwierdzone = json.loads(plik.read_text(encoding="utf-8"))
    draft, final = (json.dumps(d, ensure_ascii=False, sort_keys=True) for d in (zadanie["dokumenty"], zatwierdzone))
    log.append({
        **wpis,
        "decyzja": "zatwierdzone",
        # 1.0 = lekarz nic nie zmienił; im niżej, tym więcej poprawek
        "podobienstwo_draft_final": round(difflib.SequenceMatcher(None, draft, final).ratio(), 3),
        "uwagi_weryfikatora": len(zadanie["uwagi"]),
    })
    _zapisz(katalog / f"etap{etap}_zatwierdzone.json", zatwierdzone)
    return {"zatwierdzone": True, "dokumenty": zatwierdzone}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wizyta", type=Path, help="plik JSON z danymi wejściowymi wizyty")
    parser.add_argument("--auto", action="store_true", help="zatwierdzaj drafty bez czekania na lekarza")
    args = parser.parse_args()

    wizyta = json.loads(args.wizyta.read_text(encoding="utf-8"))
    katalog = OUT / wizyta["wizyta_id"]
    katalog.mkdir(parents=True, exist_ok=True)
    log: list[dict] = []

    graf = zbuduj_graf()
    cfg = {"configurable": {"thread_id": wizyta["wizyta_id"]}}
    wynik = graf.invoke(
        {"wizyta_id": wizyta["wizyta_id"], "pesel": wizyta["pesel"],
         "etap1_dyktowanie": _tekst_dyktowania(wizyta, 1)},
        cfg,
    )
    while "__interrupt__" in wynik:
        zadanie = wynik["__interrupt__"][0].value
        if zadanie["typ"] == "dyktowanie":
            odpowiedz = _tekst_dyktowania(wizyta, zadanie["etap"])
        else:
            odpowiedz = _zatwierdzenie(zadanie, katalog, args.auto, log)
        wynik = graf.invoke(Command(resume=odpowiedz), cfg)

    _zapisz(katalog / "dokumentacja.json", wynik["dokumentacja"])
    _zapisz(katalog / "log.json", log)
    print(f"\nGotowe: {katalog / 'dokumentacja.json'} (metryki: log.json)")


if __name__ == "__main__":
    main()
