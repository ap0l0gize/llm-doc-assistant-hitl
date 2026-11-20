"""Registry matching on commonly prescribed primary-care drugs, written the way they come out of
a dictation (Polish names, brand names, units in words, combinations, non-tablet forms).

This list is deliberately independent of the benchmark scenarios: matching rules are tuned
here, not on the visits used to measure the system. Needs the registry export in data/
(python -m src.drugs).

    python -m pytest tests/test_drugs.py -q
"""

import re

import pytest

from src.drugs import match_drug, parse_strength


def numbers(text: str | None) -> set[str]:
    return {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", (text or "").replace(" ", ""))}


# (brand, substance, strength, form, dosage) -> (Latin substance in the common name, strength numbers, form fragment)
SELECTED = [
    ((None, "amoksycylina", "500 mg", "kapsułki", None), ("amoxicillin", "500", "kapsułk")),
    ((None, "amoksycylina", "250 mg/5 ml", "zawiesina", None), ("amoxicillin", "250 5", "zawiesin")),
    ((None, "amoksycylina + kwas klawulanowy", "875 mg + 125 mg", "tabletki", None), ("clavulan", "875 125", "tablet")),
    (("Amoksiklav", None, "875 mg + 125 mg", "tabletki", None), ("clavulan", "875 125", "tablet")),
    ((None, "fenoksymetylpenicylina", "1 mln j.m.", "tabletki", None), ("phenoxymethylpenicillin", "1000000", "tablet")),
    ((None, "fenoksymetylpenicylina", "1 000 000 j.m.", "tabletki", None), ("phenoxymethylpenicillin", "1000000", "tablet")),
    ((None, "klarytromycyna", "500 mg", "tabletki powlekane", None), ("clarithromycin", "500", "tabletki powlekane")),
    ((None, "azytromycyna", "500 mg", "tabletki", None), ("azithromycin", "500", "tablet")),
    ((None, "cyprofloksacyna", "500 mg", "tabletki", None), ("ciprofloxacin", "500", "tablet")),
    ((None, "furazydyna", "50 mg", "tabletki", None), ("furazidin", "50", "tablet")),
    ((None, "lewofloksacyna", "5 mg/ml", "krople do oczu", None), ("levofloxacin", "5", "do oczu")),
    ((None, "lewotyroksyna", "50 mikrogramów", "tabletki", None), ("levothyroxin", "50", "tablet")),
    ((None, "lewotyroksyna", "75 µg", "tabletki", None), ("levothyroxin", "75", "tablet")),
    ((None, "lewotyroksyna", "0,1 mg", "tabletki", None), ("levothyroxin", "100", "tablet")),
    ((None, "budezonid + formoterol", "160 mikrogramów + 4,5 mikrograma/dawkę", "proszek do inhalacji", None),
     ("budesonid", "160 4.5", "inhal")),
    (("Symbicort Turbuhaler", "budezonid + formoterol", "160 mikrogramów + 4,5 mikrograma", "proszek do inhalacji", None),
     ("budesonid", "160 4.5", "inhal")),
    ((None, "salbutamol", "100 mikrogramów/dawkę", "aerozol inhalacyjny", None), ("salbutamol", "100", "inhal")),
    ((None, "mometazon", "50 mikrogramów/dawkę", "aerozol do nosa", None), ("mometason", "50", "do nosa")),
    ((None, "prednizon", "20 mg", "tabletki", None), ("prednison", "20", "tablet")),
    ((None, "kwas acetylosalicylowy", "75 mg", "tabletki", None), ("acetylsalicyl", "75", "tablet")),
    ((None, "kwas foliowy", "0,4 mg", "tabletki", None), ("folic", "0.4", "tablet")),
    ((None, "cholekalcyferol", "2000 j.m.", "kapsułki", None), ("cholecalciferol", "2000", "kapsułk")),
    ((None, "ketoprofen", "2,5%", "żel", None), ("ketoprofen", "25", "żel")),
    ((None, "ibuprofen", "100 mg/5 ml", "zawiesina", None), ("ibuprofen", "100 5", "zawiesin")),
    ((None, "ibuprofen", "400 mg", "tabletki", None), ("ibuprofen", "400", "tablet")),
    ((None, "paracetamol", "120 mg/5 ml", "zawiesina", None), ("paracetamol", "120 5", "zawiesin")),
    ((None, "paracetamol", "500 mg", None, "2 tabletki doraźnie"), ("paracetamol", "500", "tablet")),
    ((None, "tramadol + paracetamol", "37,5 mg + 325 mg", "tabletki", None), ("tramadol", "37.5 325", "tablet")),
    ((None, "naproksen", "500 mg", "tabletki", None), ("naproxen", "500", "tablet")),
    ((None, "metformina", "1000 mg", "tabletki", None), ("metformin", "1000", "tablet")),
    ((None, "empagliflozyna", "10 mg", "tabletki", None), ("empagliflozin", "10", "tablet")),
    ((None, "atorwastatyna", "20 mg", "tabletki", None), ("atorvastatin", "20", "tablet")),
    ((None, "ramipril", "5 mg", "kapsułki", None), ("ramipril", "5", "kapsułk")),
    ((None, "amlodypina", "10 mg", "tabletki", None), ("amlodipin", "10", "tablet")),
    ((None, "bisoprolol", "2,5 mg", "tabletki", None), ("bisoprolol", "2.5", "tablet")),
    ((None, "apiksaban", "5 mg", "tabletki powlekane", None), ("apixaban", "5", "tablet")),
    ((None, "furosemid", "40 mg", "tabletki", None), ("furosemid", "40", "tablet")),
    ((None, "spironolakton", "25 mg", "tabletki", None), ("spironolacton", "25", "tablet")),
    ((None, "pantoprazol", "20 mg", None, "1 tabletka rano na czczo"), ("pantoprazol", "20", "tablet")),
    ((None, "omeprazol", "20 mg", "kapsułki", None), ("omeprazol", "20", "kapsułk")),
    ((None, "hydroksyzyna", "25 mg", "tabletki", None), ("hydroxyzin", "25", "tablet")),
    ((None, "sertralina", "50 mg", "tabletki powlekane", None), ("sertralin", "50", "tablet")),
    (("Xonvea", "doksylamina + pirydoksyna", "10 mg + 10 mg", "tabletki", None), ("doxylamin", "10 10", "tablet")),
    ((None, "walacyklowir", "1000 mg", "tabletki", None), ("valaciclovir", "1000", "tablet")),
]

# Dictations the system must not resolve on its own: (brand, substance, strength, form, dosage) -> expected flag
FLAGGED = [
    (("Heviran", "walacyklowir", "1000 mg", "tabletki", None), "brand_substance_conflict"),
    ((None, "klarytromycyna", "500 mg", None, None), "form_not_stated"),
    ((None, "kwas foliowy", None, None, None), "choose_product"),
    ((None, "lewotyroksyna", "60 mikrogramów", "tabletki", None), "strength_not_in_registry"),
]


@pytest.mark.parametrize("query, expected", SELECTED, ids=[f"{q[0] or ''} {q[1] or ''} {q[2]}" for q, _ in SELECTED])
def test_selects_the_dictated_product(query, expected):
    substance, strength, form = expected
    result = match_drug(*query)
    selected = result["selected"]
    assert selected is not None, f"nothing selected, flags {result['flags']}, candidates " \
                                 f"{[(c['name'], c['strength'], c['form']) for c in result['candidates']]}"
    assert substance in selected["common_name"].lower()
    assert numbers(selected["strength"]) == set(strength.split()) or \
        parse_strength(selected["strength"]) == parse_strength(query[2]), selected["strength"]
    assert form in selected["form"].lower()


@pytest.mark.parametrize("query, flag", FLAGGED, ids=[f"{q[0] or ''} {q[1] or ''} {q[2]}" for q, _ in FLAGGED])
def test_does_not_resolve_ambiguous_dictation(query, flag):
    result = match_drug(*query)
    assert result["selected"] is None
    assert flag in result["flags"]


def test_vitamin_d_is_not_matched_to_diclofenac():
    # v1 matched 'witamina D' with the product Itami (diclofenac patch)
    candidates = match_drug(None, "witamina D", "2000 j.m.", "kapsułki")
    names = [c["common_name"] for c in [candidates["selected"], *candidates["candidates"]] if c]
    assert not any("Diclofenac" in n for n in names)


@pytest.mark.parametrize("dictated, registered", [
    ("50 mikrogramów", "50 mcg"),
    ("1 mln j.m.", "1 000 000 j.m."),
    ("2,5%", "25 mg/g"),
    ("500 mg na 5 ml", "100 mg/ml"),
    ("160 mikrogramów + 4,5 mikrograma/dawkę", "(160 mcg + 4,5 mcg)/dawkę inh."),
    ("1 g", "1000 mg"),
])
def test_equivalent_strengths(dictated, registered):
    assert parse_strength(dictated) == parse_strength(registered)


@pytest.mark.parametrize("dictated, registered", [("2,5 mg", "5 mg"), ("1 mln j.m.", "1,5 mln j.m."), ("50 mcg", "5 mcg")])
def test_different_strengths(dictated, registered):
    assert parse_strength(dictated) != parse_strength(registered)
