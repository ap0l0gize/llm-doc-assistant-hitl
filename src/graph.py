"""Graf wizyty: dwa etapy, każdy kończy się zatwierdzeniem przez lekarza.

Etap 1 (w trakcie wizyty):  dyktowanie -> agent recepty + agent zaleceń -> LEKARZ
Etap 2 (po wizycie):        dyktowanie -> agent karty -> weryfikator -> LEKARZ

Graf nie wie nic o interfejsie: w miejscach, gdzie potrzebny jest człowiek,
wywołuje interrupt() i czeka na Command(resume=...). Dziś odpowiada na to
run.py (pliki JSON), później ten sam graf obsłuży Streamlit.
"""

import json
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import drugs
from .llm import ustrukturyzuj
from .schemas import KartaWizyty, ReceptaLLM, Weryfikacja, Zalecenia

PACJENCI_JSON = Path(__file__).parent.parent / "data" / "pacjenci.json"

ZASADY = (
    "Jesteś asystentem dokumentacji medycznej. Porządkujesz to, co podyktował lekarz. "
    "Używasz wyłącznie informacji z dyktowania i danych pacjenta. Niczego nie dodajesz, "
    "nie dobierasz leków ani dawek i nie zgadujesz brakujących informacji."
)


class Stan(TypedDict, total=False):
    wizyta_id: str
    pesel: str
    pacjent: dict
    etap1_dyktowanie: str
    etap2_dyktowanie: str
    recepta: dict
    zalecenia: dict
    karta: dict
    uwagi: list[dict]
    etap1_zatwierdzony: bool
    etap2_zatwierdzony: bool
    komentarz: str | None  # uwagi lekarza przy odesłaniu do poprawy
    dokumentacja: dict


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _poprawka(stan: Stan, poprzedni_draft: dict | None) -> str:
    if not stan.get("komentarz") or poprzedni_draft is None:
        return ""
    return (
        f"\n\nPoprzednia wersja:\n{_json(poprzedni_draft)}"
        f"\n\nLekarz odesłał ją do poprawy z komentarzem:\n{stan['komentarz']}"
    )


def wczytaj_pacjenta(stan: Stan) -> dict:
    pacjenci = json.loads(PACJENCI_JSON.read_text(encoding="utf-8"))
    pacjent = next((p for p in pacjenci if p["pesel"] == stan["pesel"]), None)
    if pacjent is None:
        raise ValueError(f"Brak pacjenta o PESEL {stan['pesel']} w {PACJENCI_JSON.name}")
    return {"pacjent": pacjent}


def agent_recepty(stan: Stan) -> dict:
    wynik = ustrukturyzuj(
        ReceptaLLM,
        ZASADY + " Wyciągnij z dyktowania leki do projektu recepty.",
        f"Dyktowanie:\n{stan['etap1_dyktowanie']}" + _poprawka(stan, stan.get("recepta")),
    )
    pozycje = []
    for lek in wynik.pozycje:
        kandydaci = drugs.dopasuj(lek.nazwa, lek.dawka)
        braki = [pole for pole in ("dawka", "dawkowanie", "ilosc") if getattr(lek, pole) is None]
        if not kandydaci:
            braki.append("brak dopasowania w bazie leków")
        pozycje.append({
            "podyktowano": lek.model_dump(),
            # Lekarz zostawia jednego kandydata albo poprawia go ręcznie.
            "wybrany_lek": kandydaci[0] if kandydaci else None,
            "pozostali_kandydaci": kandydaci[1:],
            "do_uzupelnienia": braki,
        })
    return {"recepta": {"pozycje": pozycje}}


def agent_zalecen(stan: Stan) -> dict:
    wynik = ustrukturyzuj(
        Zalecenia,
        ZASADY + " Przygotuj zalecenia dla pacjenta prostym, zrozumiałym językiem.",
        f"Dyktowanie:\n{stan['etap1_dyktowanie']}" + _poprawka(stan, stan.get("zalecenia")),
    )
    return {"zalecenia": wynik.model_dump()}


def przeglad_etap1(stan: Stan) -> dict:
    odpowiedz = interrupt({
        "typ": "zatwierdzenie",
        "etap": 1,
        "dokumenty": {"recepta": stan["recepta"], "zalecenia": stan["zalecenia"]},
        "uwagi": [],
    })
    if not odpowiedz["zatwierdzone"]:
        return {"etap1_zatwierdzony": False, "komentarz": odpowiedz["komentarz"]}
    return {**odpowiedz["dokumenty"], "etap1_zatwierdzony": True, "komentarz": None}


def czekaj_na_notatke(stan: Stan) -> dict:
    """Pacjent wyszedł z receptą; graf czeka, aż lekarz podyktuje notatkę z wizyty."""
    return {"etap2_dyktowanie": interrupt({"typ": "dyktowanie", "etap": 2})}


def agent_karty(stan: Stan) -> dict:
    wynik = ustrukturyzuj(
        KartaWizyty,
        ZASADY + " Przygotuj wpis do karty wizyty ambulatoryjnej językiem dokumentacji medycznej.",
        f"Dane pacjenta:\n{_json(stan['pacjent'])}\n\nDyktowanie po wizycie:\n{stan['etap2_dyktowanie']}"
        + _poprawka(stan, stan.get("karta")),
    )
    return {"karta": wynik.model_dump()}


def weryfikator(stan: Stan) -> dict:
    wynik = ustrukturyzuj(
        Weryfikacja,
        "Sprawdzasz projekt karty wizyty. Zgłoś każdą informację w karcie, której nie ma "
        "w dyktowaniu ani w danych pacjenta, oraz każdą istotną informację z dyktowania, "
        "której w karcie brakuje. Nie oceniaj decyzji lekarza.",
        f"Dane pacjenta:\n{_json(stan['pacjent'])}\n\nDyktowanie:\n{stan['etap2_dyktowanie']}"
        f"\n\nProjekt karty:\n{_json(stan['karta'])}",
    )
    return {"uwagi": [u.model_dump() for u in wynik.uwagi]}


def przeglad_etap2(stan: Stan) -> dict:
    odpowiedz = interrupt({
        "typ": "zatwierdzenie",
        "etap": 2,
        "dokumenty": {"karta": stan["karta"]},
        "uwagi": stan["uwagi"],
    })
    if not odpowiedz["zatwierdzone"]:
        return {"etap2_zatwierdzony": False, "komentarz": odpowiedz["komentarz"]}
    return {**odpowiedz["dokumenty"], "etap2_zatwierdzony": True, "komentarz": None}


def zloz_dokumentacje(stan: Stan) -> dict:
    # Zatwierdzone w etapie 1 leki i zalecenia trafiają do karty bez ponownego dyktowania.
    return {"dokumentacja": {
        "wizyta_id": stan["wizyta_id"],
        "pacjent": stan["pacjent"],
        "karta_wizyty": {**stan["karta"], "zalecenia": stan["zalecenia"], "przepisane_leki": stan["recepta"]},
    }}


def zbuduj_graf(checkpointer=None):
    g = StateGraph(Stan)
    for wezel in (wczytaj_pacjenta, agent_recepty, agent_zalecen, przeglad_etap1,
                  czekaj_na_notatke, agent_karty, weryfikator, przeglad_etap2, zloz_dokumentacje):
        g.add_node(wezel.__name__, wezel)

    g.add_edge(START, "wczytaj_pacjenta")
    g.add_edge("wczytaj_pacjenta", "agent_recepty")
    g.add_edge("wczytaj_pacjenta", "agent_zalecen")
    g.add_edge(["agent_recepty", "agent_zalecen"], "przeglad_etap1")
    g.add_conditional_edges(
        "przeglad_etap1",
        lambda s: ["czekaj_na_notatke"] if s["etap1_zatwierdzony"] else ["agent_recepty", "agent_zalecen"],
        ["czekaj_na_notatke", "agent_recepty", "agent_zalecen"],
    )
    g.add_edge("czekaj_na_notatke", "agent_karty")
    g.add_edge("agent_karty", "weryfikator")
    g.add_edge("weryfikator", "przeglad_etap2")
    g.add_conditional_edges(
        "przeglad_etap2",
        lambda s: "zloz_dokumentacje" if s["etap2_zatwierdzony"] else "agent_karty",
        ["zloz_dokumentacje", "agent_karty"],
    )
    g.add_edge("zloz_dokumentacje", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
