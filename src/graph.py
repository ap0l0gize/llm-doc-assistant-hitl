"""Visit graph: two stages, each ending with the doctor's review.

Stage 1 (during the visit):  dictation -> prescription agent + instructions agent -> DOCTOR
Stage 2 (after the visit):   dictation -> visit note agent -> verifier -> DOCTOR

The doctor verifies the draft, corrects it if needed and approves it; the visit then
moves on to the next stage. Drafts are never sent back to the model: every change
after the draft is the doctor's.

The graph knows nothing about the user interface: wherever a human is needed it
calls interrupt() and waits for Command(resume=<approved documents>). Each review
point also carries notes for the doctor ("issues"); their "field" is a path in the
document the doctor edits (src/review_format.py), e.g. "prescription.1.quantity".

Prompts and notes are in Polish, since the assistant produces Polish medical documentation.
"""

import json
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import drugs
from .llm import generate_structured
from .schemas import DictatedPrescription, PatientInstructions, Verification, VisitNote

PATIENTS_JSON = Path(__file__).parent.parent / "data" / "patients.json"

BASE_RULES = (
    "Jesteś asystentem dokumentacji medycznej. Porządkujesz to, co podyktował lekarz. "
    "Używasz wyłącznie informacji z dyktowania i danych pacjenta. Niczego nie dodajesz, "
    "nie dobierasz leków ani dawek i nie zgadujesz brakujących informacji. Nie doprecyzowujesz "
    "szczegółów, których nie ma w dyktowaniu. Pola zawierają wyłącznie treść dokumentu - bez komentarzy "
    "o tym, czego nie podano, i bez uwag dla lekarza."
)

# Notes for the doctor about one prescription item: missing dictated fields and the
# reason the registry matching did not select a product (src/drugs.py, match_drug).
MISSING_FIELD_NOTES = {
    "strength": "nie podano mocy",
    "dosage": "nie podano dawkowania",
    "quantity": "nie podano ilości",
}
REGISTRY_FLAG_NOTES = {
    "brand_substance_conflict": "nazwa handlowa {brand} to produkt innej substancji ({other}) niż podyktowana - wybierz produkt",
    "brand_not_found": "nie znaleziono nazwy handlowej {brand} w rejestrze - produkt dobrano po substancji, sprawdź go",
    "no_registry_match": "nie znaleziono leku w rejestrze - wpisz produkt ręcznie",
    "choose_product": "bez podanej mocy nie da się ustalić produktu - wybierz produkt",
    "strength_not_in_registry": "w rejestrze nie ma produktu o mocy {strength} - sprawdź moc i wybierz produkt",
    "form_not_in_registry": "w rejestrze nie ma tej mocy w postaci „{form}” - wybierz produkt",
    "form_not_stated": "lek występuje w tej mocy w kilku postaciach - wybierz produkt",
}


class VisitState(TypedDict, total=False):
    visit_id: str
    pesel: str
    patient: dict
    stage1_dictation: str
    stage2_dictation: str
    prescription: dict
    patient_instructions: dict
    visit_note: dict
    issues: list[dict]  # verifier's notes for stage 2
    documentation: dict


def _to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _patient_summary(patient: dict) -> str:
    """Patient record as plain Polish text. Given raw JSON, the model wrote things like
    'brak danych o alergiach (lista pusta)' into the visit note."""
    listing = lambda values, none: "; ".join(values) if values else none
    return (
        f"Pacjent: {patient['first_name']} {patient['last_name']}, data urodzenia {patient['date_of_birth']}\n"
        f"Choroby przewlekłe: {listing(patient['chronic_conditions'], 'brak')}\n"
        f"Leki stałe: {listing(patient['regular_medications'], 'brak')}\n"
        f"Alergie: {listing(patient['allergies'], 'brak zgłoszonych')}"
    )


def load_patient(state: VisitState) -> dict:
    patients = json.loads(PATIENTS_JSON.read_text(encoding="utf-8"))
    patient = next((p for p in patients if p["pesel"] == state["pesel"]), None)
    if patient is None:
        raise ValueError(f"No patient with PESEL {state['pesel']} in {PATIENTS_JSON.name}")
    return {"patient": patient}


def prescription_agent(state: VisitState) -> dict:
    result = generate_structured(
        DictatedPrescription,
        BASE_RULES + " Wyciągnij z dyktowania leki do projektu recepty. Leki, które pacjent ma tylko "
        "kontynuować bez zmian (np. 'X bez zmian', 'X kontynuować'), nie są nowym zleceniem - pomiń je.",
        f"Dyktowanie:\n{state['stage1_dictation']}",
    )
    items = []
    for drug in result.items:
        match = drugs.match_drug(drug.brand_name, drug.substance, drug.strength, drug.form, drug.dosage)
        items.append({
            "dictated": drug.model_dump(),
            # Selected only when the dictation determines the product; otherwise None and
            # registry_flags say why. The doctor confirms, picks a candidate or corrects it.
            "selected_drug": match["selected"],
            "other_candidates": match["candidates"],
            "missing_fields": [field for field in ("strength", "dosage", "quantity") if getattr(drug, field) is None],
            "registry_flags": match["flags"],
        })
    return {"prescription": {"items": items}}


def instructions_agent(state: VisitState) -> dict:
    result = generate_structured(
        PatientInstructions,
        BASE_RULES + " Przygotuj zalecenia dla pacjenta prostym, zrozumiałym językiem. Nie dodawaj ogólnych "
        "zaleceń, których lekarz nie podyktował. Leki wymieniaj z nazwy, tak jak podał lekarz, i nie dopisuj "
        "ich wskazań.",
        f"Dyktowanie:\n{state['stage1_dictation']}",
    )
    return {"patient_instructions": result.model_dump()}


def prescription_issues(prescription: dict) -> list[dict]:
    """Deterministic notes for the doctor about the prescription draft (no model call)."""
    issues = []
    for i, item in enumerate(prescription["items"]):
        d = item["dictated"]
        name = d.get("substance") or d.get("brand_name") or f"pozycja {i + 1}"
        for field in item["missing_fields"]:
            issues.append({"field": f"prescription.{i}.{field}", "problem": f"{name}: {MISSING_FIELD_NOTES[field]}"})
        for flag in item.get("registry_flags", []):
            other = ", ".join(sorted({c["common_name"] for c in item.get("other_candidates", [])})) or "?"
            note = REGISTRY_FLAG_NOTES.get(flag, flag).format(
                brand=d.get("brand_name"), strength=d.get("strength"), form=d.get("form"), other=other)
            issues.append({"field": f"prescription.{i}.product", "problem": f"{name}: {note}"})
    return issues


def review_stage1(state: VisitState) -> dict:
    documents = interrupt({
        "type": "approval",
        "stage": 1,
        "documents": {"prescription": state["prescription"], "patient_instructions": state["patient_instructions"]},
        "issues": prescription_issues(state["prescription"]),
    })
    return {"prescription": documents["prescription"], "patient_instructions": documents["patient_instructions"]}


def await_stage2_dictation(state: VisitState) -> dict:
    """The patient has left with the prescription; wait for the doctor to dictate the visit note."""
    return {"stage2_dictation": interrupt({"type": "dictation", "stage": 2})}


def visit_note_agent(state: VisitState) -> dict:
    result = generate_structured(
        VisitNote,
        BASE_RULES + " Przygotuj wpis do karty wizyty ambulatoryjnej językiem dokumentacji medycznej.",
        f"Dane pacjenta:\n{_patient_summary(state['patient'])}\n\nDyktowanie po wizycie:\n{state['stage2_dictation']}",
    )
    return {"visit_note": result.model_dump()}


def verifier(state: VisitState) -> dict:
    result = generate_structured(
        Verification,
        "Sprawdzasz projekt karty wizyty. Zgłoś każdą informację w karcie, której nie ma "
        "w dyktowaniu ani w danych pacjenta, oraz każdą istotną informację z dyktowania, "
        "której w karcie brakuje. Nie oceniaj decyzji lekarza. Nie zgłaszaj samych zmian zapisu "
        "(liczby zamiast słów, standardowe jednostki, skróty medyczne) ani zgodności - tylko rozbieżności.",
        f"Dane pacjenta:\n{_patient_summary(state['patient'])}\n\nDyktowanie:\n{state['stage2_dictation']}"
        f"\n\nProjekt karty:\n{_to_json(state['visit_note'])}",
    )
    # The verifier names a field of the note; turn it into a path in the edited document.
    fields = VisitNote.model_fields
    return {"issues": [
        {"field": f"visit_note.{i.field}" if i.field in fields else "visit_note", "problem": i.problem}
        for i in result.issues
    ]}


def review_stage2(state: VisitState) -> dict:
    documents = interrupt({
        "type": "approval",
        "stage": 2,
        "documents": {"visit_note": state["visit_note"]},
        "issues": state["issues"],
    })
    return {"visit_note": documents["visit_note"]}


def assemble_documentation(state: VisitState) -> dict:
    # Drugs and instructions approved in stage 1 go into the visit note without being dictated again.
    return {"documentation": {
        "visit_id": state["visit_id"],
        "patient": state["patient"],
        "visit_note": {
            **state["visit_note"],
            "patient_instructions": state["patient_instructions"],
            "prescribed_drugs": state["prescription"],
        },
    }}


def build_graph(checkpointer=None):
    g = StateGraph(VisitState)
    for node in (load_patient, prescription_agent, instructions_agent, review_stage1,
                 await_stage2_dictation, visit_note_agent, verifier, review_stage2, assemble_documentation):
        g.add_node(node.__name__, node)

    g.add_edge(START, "load_patient")
    g.add_edge("load_patient", "prescription_agent")
    g.add_edge("load_patient", "instructions_agent")
    g.add_edge(["prescription_agent", "instructions_agent"], "review_stage1")
    g.add_edge("review_stage1", "await_stage2_dictation")
    g.add_edge("await_stage2_dictation", "visit_note_agent")
    g.add_edge("visit_note_agent", "verifier")
    g.add_edge("verifier", "review_stage2")
    g.add_edge("review_stage2", "assemble_documentation")
    g.add_edge("assemble_documentation", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
