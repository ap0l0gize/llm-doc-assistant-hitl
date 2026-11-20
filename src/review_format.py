"""The document the doctor edits at each review point, and the changes the doctor made.

The agents' output (src/graph.py) carries technical detail: full registry records,
matching flags, candidate lists. The doctor gets a readable version of the same
content instead, with the selected product as one line:

    "product": "Eliquis · 5 mg · Tabletki powlekane · RPL 100230018",
    "candidates": ["Apixaban Aurovitas · 5 mg · Tabletki powlekane · RPL 100412345", ...]

To change the product the doctor pastes a candidate line (or writes any product with
its RPL number). "doctor_notes" is where the doctor describes the corrections, following
the classification codebook (docs/benchmark/codebook.md):

    {"path": "prescription.1.quantity", "category": "recepta", "severity": "umiarkowany",
     "source": "dyktowanie", "reason": "..."}

Paths use the keys of this document with list indexes from 0, the same paths the
system's notes (issues) use. Messages for the doctor are in Polish.

The visit note has an empty "icd10" field: the system does not propose codes, the
doctor enters the code with the diagnosis. Entering it is the doctor's own work,
not a correction of the system, so it is not counted among the changes.
"""

import re
from difflib import SequenceMatcher

from rapidfuzz.distance import Levenshtein

from . import drugs

# Classification of the doctor's corrections: docs/benchmark/codebook.md
NOTE_CATEGORIES = ("pominięcie", "treść spoza dyktowania", "błędny fakt", "recepta", "zalecenia", "forma/język")
SEVERITIES = ("istotny", "umiarkowany", "kosmetyczny")
SOURCES = ("system", "dyktowanie", "rejestr")
# The reviewer's assessment of a whole visit (analysis.json), also in the codebook.
VERDICTS = ("gotowy bez zmian", "drobne poprawki", "istotne poprawki", "nie do użycia")
RATING_CRITERIA = ("dokładność", "kompletność", "zwięzłość", "zrozumiałość dla pacjenta", "spójność wewnętrzna")


def analysis_template(visit_id: str) -> dict:
    return {"visit_id": visit_id, "summary": "", "strengths": [], "weaknesses": [], "verdict": None,
            "ratings": dict.fromkeys(RATING_CRITERIA)}


def analysis_gaps(analysis: dict) -> list[str]:
    """What is still missing in a filled-in analysis.json (empty list = complete)."""
    gaps = []
    if not str(analysis.get("summary") or "").strip():
        gaps.append("summary")
    if not analysis.get("strengths") and not analysis.get("weaknesses"):
        gaps.append("strengths/weaknesses")
    if analysis.get("verdict") not in VERDICTS:
        gaps.append("verdict")
    ratings = analysis.get("ratings") or {}
    gaps += [f"ratings.{c}" for c in RATING_CRITERIA if ratings.get(c) not in (1, 2, 3, 4, 5)]
    return gaps
DICTATED_FIELDS = ("substance", "brand_name", "strength", "form", "dosage", "quantity")
VISIT_NOTE_FIELDS = ("history", "physical_examination", "diagnosis")
DOCUMENTS = ("prescription", "patient_instructions", "visit_note")
# Not compared between the draft and the approved document: what is shown to the doctor
# but is not documentation (candidates, notes), and what only the doctor fills in (icd10).
NOT_DOCUMENT = {"candidates", "doctor_notes", "icd10"}
RPL_ID = re.compile(r"RPL\s*(\d+)")
ICD10_CODE = re.compile(r"[A-Z]\d{2}(\.\d{1,2})?")


class ReviewError(ValueError):
    """The edited document cannot be approved; the message (Polish) is shown to the doctor."""


# --- draft for the doctor ----------------------------------------------------

def product_line(product: dict) -> str:
    return f"{product['name']} · {product['strength']} · {product['form']} · RPL {product['rpl_id']}"


def to_editable(stage: int, documents: dict) -> dict:
    if stage == 1:
        return {
            "prescription": [
                {
                    **{field: item["dictated"].get(field) for field in DICTATED_FIELDS},
                    "product": product_line(item["selected_drug"]) if item["selected_drug"] else None,
                    "candidates": [product_line(c) for c in item.get("other_candidates", [])],
                }
                for item in documents["prescription"]["items"]
            ],
            "patient_instructions": documents["patient_instructions"],
            "doctor_notes": [],
        }
    return {"visit_note": {**{field: documents["visit_note"].get(field) for field in VISIT_NOTE_FIELDS},
                           "icd10": documents["visit_note"].get("icd10")},
            "doctor_notes": []}


# --- approved document back to the system's format --------------------------

def _text(container: dict, key: str, where: str, required: bool = False) -> str | None:
    value = container.get(key)
    if value is not None and not isinstance(value, str):
        raise ReviewError(f"{where}: pole „{key}” musi być tekstem albo null")
    if required and not (value or "").strip():
        raise ReviewError(f"{where}: uzupełnij pole „{key}”")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _texts(container: dict, key: str, where: str) -> list[str]:
    value = container.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ReviewError(f"{where}: pole „{key}” musi być listą tekstów")
    return [v.strip() for v in value if v.strip()]


def _section(editable: dict, key: str, kind: type, label: str):
    value = editable.get(key)
    if not isinstance(value, kind):
        raise ReviewError(f"brak sekcji „{key}” ({label}) albo ma zły typ")
    return value


def _prescription_item(i: int, item) -> dict:
    where = f"recepta, pozycja {i + 1}"
    if not isinstance(item, dict):
        raise ReviewError(f"{where}: pozycja recepty musi być obiektem {{...}}")
    dictated = {field: _text(item, field, where) for field in DICTATED_FIELDS}
    if not (dictated["substance"] or dictated["brand_name"]):
        raise ReviewError(f"{where}: podaj substancję czynną albo nazwę handlową")
    where = f"{where} ({dictated['substance'] or dictated['brand_name']})"
    _text(item, "dosage", where, required=True)
    _text(item, "quantity", where, required=True)
    line = _text(item, "product", where)
    if line is None:
        raise ReviewError(f"{where}: wybierz produkt - wklej linię z „candidates” albo wpisz produkt z numerem RPL")
    match = RPL_ID.search(line)
    product = drugs.product_by_id(match.group(1)) if match else None
    if product is None:
        raise ReviewError(f"{where}: nie rozpoznano produktu „{line}” - linia musi zawierać numer RPL "
                          f"istniejącego produktu (np. „... · RPL 100230018”)")
    return {
        "dictated": dictated,
        "selected_drug": product,
        "other_candidates": [],
        "missing_fields": [field for field in ("strength", "dosage", "quantity") if not dictated[field]],
        "registry_flags": [],  # resolved by the doctor
    }


def _doctor_notes(editable: dict) -> list[dict]:
    notes = editable.get("doctor_notes", [])
    if not isinstance(notes, list):
        raise ReviewError("„doctor_notes” musi być listą")
    for n, note in enumerate(notes, 1):
        where = f"doctor_notes, uwaga {n}"
        if not isinstance(note, dict):
            raise ReviewError(f"{where}: uwaga musi być obiektem {{path, category, severity, source, reason}}")
        _text(note, "path", where, required=True)
        _text(note, "reason", where, required=True)
        for key, allowed in (("category", NOTE_CATEGORIES), ("severity", SEVERITIES), ("source", SOURCES)):
            if note.get(key) not in allowed:
                raise ReviewError(f"{where}: {key} musi być jednym z: {', '.join(allowed)} "
                                  f"(definicje: docs/benchmark/codebook.md)")
    return notes


def from_editable(stage: int, editable) -> tuple[dict, list[dict]]:
    """Validates the document the doctor saved and converts it back to the system's format.
    Returns (documents, doctor_notes); raises ReviewError with a message for the doctor."""
    if not isinstance(editable, dict):
        raise ReviewError("plik musi zawierać obiekt JSON {...}")
    notes = _doctor_notes(editable)
    if stage == 1:
        items = _section(editable, "prescription", list, "recepta")
        instructions = _section(editable, "patient_instructions", dict, "zalecenia")
        where = "zalecenia"
        return {
            "prescription": {"items": [_prescription_item(i, item) for i, item in enumerate(items)]},
            "patient_instructions": {
                "recommendations": _texts(instructions, "recommendations", where),
                "warning_signs": _texts(instructions, "warning_signs", where),
                "follow_up": _text(instructions, "follow_up", where),
            },
        }, notes
    note = _section(editable, "visit_note", dict, "karta wizyty")
    where = "karta wizyty"
    icd10 = (_text(note, "icd10", where) or "").upper()
    if not ICD10_CODE.fullmatch(icd10):
        raise ReviewError(f"{where}: wpisz kod ICD-10 rozpoznania w polu „icd10” (sam kod, np. „J18.9”)"
                          + (f" - „{icd10}” nie jest kodem ICD-10" if icd10 else ""))
    return {"visit_note": {
        "history": _text(note, "history", where, required=True),
        "physical_examination": _text(note, "physical_examination", where, required=True),
        "diagnosis": _text(note, "diagnosis", where),
        "icd10": icd10,
    }}, notes


# --- what the doctor changed -----------------------------------------------

def _join(path: str, key) -> str:
    return f"{path}.{key}" if path else str(key)


def _blank(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _identity(value):
    """Aligns list elements: a prescription item by its drug, a text by itself."""
    if isinstance(value, dict):
        return (value.get("substance") or value.get("brand_name") or "").strip().lower()
    return value


def diff(before, after, path: str = "") -> list[dict]:
    """Changes between the draft and the approved document: [{path, kind, before, after}],
    kind = modified | added | deleted. Candidates and the doctor's notes are not compared."""
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in list(before) + [k for k in after if k not in before]:
            if key not in NOT_DOCUMENT:
                changes += diff(before.get(key), after.get(key), _join(path, key))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        changes = []
        matcher = SequenceMatcher(None, [_identity(v) for v in before], [_identity(v) for v in after], autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            paired = min(i2 - i1, j2 - j1) if tag in ("equal", "replace") else 0
            for k in range(paired):
                changes += diff(before[i1 + k], after[j1 + k], _join(path, i1 + k))
            changes += [{"path": _join(path, k), "kind": "deleted", "before": before[k], "after": None}
                        for k in range(i1 + paired, i2)]
            changes += [{"path": _join(path, k), "kind": "added", "before": None, "after": after[k]}
                        for k in range(j1 + paired, j2)]
        return changes
    if (_blank(before) and _blank(after)) or before == after:
        return []
    kind = "added" if _blank(before) else "deleted" if _blank(after) else "modified"
    return [{"path": path, "kind": kind, "before": before, "after": after}]


def _leaves(value) -> list[str]:
    if isinstance(value, dict):
        return [s for k, v in value.items() if k not in NOT_DOCUMENT for s in _leaves(v)]
    if isinstance(value, list):
        return [s for v in value for s in _leaves(v)]
    return [str(value)] if not _blank(value) else []


def text_chars(value) -> int:
    """Characters of document text (what the doctor reads), without candidates and notes."""
    return sum(len(s) for s in _leaves(value))


def is_product_choice(change: dict) -> bool:
    return change["path"].endswith(".product")


def chars_edited(change: dict) -> int:
    """Edit distance of one change: characters the doctor had to type or delete. Choosing
    a product (pasting a candidate line) is a choice, not typing, and counts as 0."""
    if is_product_choice(change):
        return 0
    before, after = " ".join(_leaves(change["before"])), " ".join(_leaves(change["after"]))
    return Levenshtein.distance(before, after)


def document_of(path: str) -> str:
    return path.split(".")[0]


def on_path(a: str, b: str) -> bool:
    """True if one path contains the other ('prescription.1' and 'prescription.1.quantity')."""
    pa, pb = a.split("."), b.split(".")
    n = min(len(pa), len(pb))
    return pa[:n] == pb[:n]


def pointed_by_issue(change: dict, issues: list[dict]) -> bool:
    return any(on_path(change["path"], issue["field"]) for issue in issues)


def has_note(change: dict, notes: list[dict]) -> bool:
    return any(on_path(change["path"], note["path"]) for note in notes)


# --- terminal summary -----------------------------------------------------

def summary(stage: int, editable: dict, issues: list[dict]) -> list[str]:
    lines = []
    if stage == 1:
        lines.append("RECEPTA")
        for i, item in enumerate(editable["prescription"], 1):
            name = item.get("substance") or item.get("brand_name")
            brand = f" ({item['brand_name']})" if item.get("brand_name") and item.get("substance") else ""
            lines.append(f"  {i}. {name}{brand} {item.get('strength') or '—'} · {item.get('dosage') or '—'}"
                         f" · ilość: {item.get('quantity') or '—'}")
            lines.append(f"     produkt: {item.get('product') or '— nie wybrano —'}")
        if not editable["prescription"]:
            lines.append("  (pusta)")
        instr = editable["patient_instructions"]
        lines.append(f"ZALECENIA: {len(instr['recommendations'])} · OBJAWY ALARMOWE: {len(instr['warning_signs'])}"
                     f" · KONTROLA: {instr.get('follow_up') or '—'}")
    else:
        note = editable["visit_note"]
        lines += ["KARTA WIZYTY", f"  rozpoznanie: {note.get('diagnosis') or '—'}",
                  f"  ICD-10: {note.get('icd10') or '— (wpisuje lekarz w polu „icd10”)'}"]
    lines.append("UWAGI SYSTEMU:" if issues else "UWAGI SYSTEMU: brak")
    lines += [f"  ! [{issue['field']}] {issue['problem']}" for issue in issues]
    return lines
