"""Visit graph: two stages, each ending with the doctor's approval.

Stage 1 (during the visit):  dictation -> prescription agent + instructions agent -> DOCTOR
Stage 2 (after the visit):   dictation -> visit note agent -> verifier -> DOCTOR

The graph knows nothing about the user interface: wherever a human is needed
it calls interrupt() and waits for Command(resume=...). Today run.py answers
(through JSON files); later the same graph will be driven by Streamlit.

Prompts are in Polish, since the assistant produces Polish medical documentation.
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
    "nie dobierasz leków ani dawek i nie zgadujesz brakujących informacji."
)


class VisitState(TypedDict, total=False):
    visit_id: str
    pesel: str
    patient: dict
    stage1_dictation: str
    stage2_dictation: str
    prescription: dict
    patient_instructions: dict
    visit_note: dict
    issues: list[dict]
    stage1_approved: bool
    stage2_approved: bool
    comment: str | None  # doctor's comment when sending a draft back for revision
    documentation: dict


def _to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _revision_request(state: VisitState, previous_draft: dict | None) -> str:
    if not state.get("comment") or previous_draft is None:
        return ""
    return (
        f"\n\nPoprzednia wersja:\n{_to_json(previous_draft)}"
        f"\n\nLekarz odesłał ją do poprawy z komentarzem:\n{state['comment']}"
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
        BASE_RULES + " Wyciągnij z dyktowania leki do projektu recepty.",
        f"Dyktowanie:\n{state['stage1_dictation']}" + _revision_request(state, state.get("prescription")),
    )
    items = []
    for drug in result.items:
        candidates = drugs.match_drug(drug.name, drug.strength)
        missing = [field for field in ("strength", "dosage", "quantity") if getattr(drug, field) is None]
        if not candidates:
            missing.append("no_registry_match")
        items.append({
            "dictated": drug.model_dump(),
            # The doctor keeps one candidate or corrects it by hand.
            "selected_drug": candidates[0] if candidates else None,
            "other_candidates": candidates[1:],
            "missing_fields": missing,
        })
    return {"prescription": {"items": items}}


def instructions_agent(state: VisitState) -> dict:
    result = generate_structured(
        PatientInstructions,
        BASE_RULES + " Przygotuj zalecenia dla pacjenta prostym, zrozumiałym językiem.",
        f"Dyktowanie:\n{state['stage1_dictation']}" + _revision_request(state, state.get("patient_instructions")),
    )
    return {"patient_instructions": result.model_dump()}


def review_stage1(state: VisitState) -> dict:
    response = interrupt({
        "type": "approval",
        "stage": 1,
        "documents": {"prescription": state["prescription"], "patient_instructions": state["patient_instructions"]},
        "issues": [],
    })
    if not response["approved"]:
        return {"stage1_approved": False, "comment": response["comment"]}
    return {**response["documents"], "stage1_approved": True, "comment": None}


def await_stage2_dictation(state: VisitState) -> dict:
    """The patient has left with the prescription; wait for the doctor to dictate the visit note."""
    return {"stage2_dictation": interrupt({"type": "dictation", "stage": 2})}


def visit_note_agent(state: VisitState) -> dict:
    result = generate_structured(
        VisitNote,
        BASE_RULES + " Przygotuj wpis do karty wizyty ambulatoryjnej językiem dokumentacji medycznej.",
        f"Dane pacjenta:\n{_to_json(state['patient'])}\n\nDyktowanie po wizycie:\n{state['stage2_dictation']}"
        + _revision_request(state, state.get("visit_note")),
    )
    return {"visit_note": result.model_dump()}


def verifier(state: VisitState) -> dict:
    result = generate_structured(
        Verification,
        "Sprawdzasz projekt karty wizyty. Zgłoś każdą informację w karcie, której nie ma "
        "w dyktowaniu ani w danych pacjenta, oraz każdą istotną informację z dyktowania, "
        "której w karcie brakuje. Nie oceniaj decyzji lekarza.",
        f"Dane pacjenta:\n{_to_json(state['patient'])}\n\nDyktowanie:\n{state['stage2_dictation']}"
        f"\n\nProjekt karty:\n{_to_json(state['visit_note'])}",
    )
    return {"issues": [issue.model_dump() for issue in result.issues]}


def review_stage2(state: VisitState) -> dict:
    response = interrupt({
        "type": "approval",
        "stage": 2,
        "documents": {"visit_note": state["visit_note"]},
        "issues": state["issues"],
    })
    if not response["approved"]:
        return {"stage2_approved": False, "comment": response["comment"]}
    return {**response["documents"], "stage2_approved": True, "comment": None}


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
    g.add_conditional_edges(
        "review_stage1",
        lambda s: ["await_stage2_dictation"] if s["stage1_approved"] else ["prescription_agent", "instructions_agent"],
        ["await_stage2_dictation", "prescription_agent", "instructions_agent"],
    )
    g.add_edge("await_stage2_dictation", "visit_note_agent")
    g.add_edge("visit_note_agent", "verifier")
    g.add_edge("verifier", "review_stage2")
    g.add_conditional_edges(
        "review_stage2",
        lambda s: "assemble_documentation" if s["stage2_approved"] else "visit_note_agent",
        ["assemble_documentation", "visit_note_agent"],
    )
    g.add_edge("assemble_documentation", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
