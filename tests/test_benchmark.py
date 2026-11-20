"""Grouping of the doctor's changes into corrections (benchmark.py), on a synthetic review log."""

from benchmark import annotated_changes, corrections


def change(path, before, after, chars=1):
    return {"path": path, "kind": "modified", "before": before, "after": after, "chars_edited": chars}


def note(path, severity="umiarkowany", source="system"):
    return {"path": path, "category": "recepta", "severity": severity, "source": source, "reason": path}


ENTRY = {
    "stage": 1,
    "changes": [
        change("prescription.0.substance", "amoksycylina", "klarytromycyna", 10),
        change("prescription.0.quantity", None, "20 tabletek", 11),
        change("prescription.0.product", None, "Klarmin", 0),
        change("prescription.1.strength", "500", "500 mg", 3),
        change("prescription.1.product", None, "Paracetamol DOZ", 0),
        change("patient_instructions.follow_up", "a", "b"),
    ],
    "issues": [{"field": "prescription.0.quantity", "problem": "nie podano ilości"},
               {"field": "prescription.1.product", "problem": "wybierz produkt"}],
    "doctor_notes": [
        note("prescription.0", severity="istotny", source="dyktowanie"),
        note("prescription.0.quantity", source="dyktowanie"),
        note("prescription.1"),
    ],
}


def by_path(rows):
    return {row["path"]: row for row in rows}


def test_one_row_per_note():
    assert [row["path"] for row in corrections(ENTRY)] == ["prescription.0", "prescription.0.quantity", "prescription.1"]


def test_field_goes_to_the_most_specific_note():
    rows = by_path(corrections(ENTRY))
    assert rows["prescription.0"]["n_fields"] == 2  # substance, product
    assert rows["prescription.0.quantity"]["n_fields"] == 1
    assert rows["prescription.0"]["chars_edited"] == 10


def test_detection_per_correction():
    rows = by_path(corrections(ENTRY))
    assert rows["prescription.0"]["detected_by"] == "lekarz"  # no system note on substance or product
    assert rows["prescription.0.quantity"]["detected_by"] == "uwaga systemu"
    assert rows["prescription.1"]["detected_by"] == "uwaga systemu"  # the product was flagged


def test_before_after():
    rows = by_path(corrections(ENTRY))
    assert rows["prescription.0.quantity"]["before"] is None and rows["prescription.0.quantity"]["after"] == "20 tabletek"
    assert rows["prescription.1"]["before"] == {"strength": "500", "product": None}
    assert rows["prescription.1"]["after"] == {"strength": "500 mg", "product": "Paracetamol DOZ"}


def test_verifier_in_stage_2():
    entry = {"stage": 2, "changes": [change("visit_note.history", "a", "b")],
             "issues": [{"field": "visit_note.history", "problem": "x"}], "doctor_notes": [note("visit_note.history")]}
    assert corrections(entry)[0]["detected_by"] == "weryfikator"


def test_changed_field_without_a_note():
    fields = by_path(annotated_changes(ENTRY))
    assert fields["patient_instructions.follow_up"]["note_path"] is None
    assert fields["patient_instructions.follow_up"]["category"] is None
    assert fields["prescription.0.quantity"]["note_path"] == "prescription.0.quantity"
