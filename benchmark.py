"""Benchmark: how much the doctor has to intervene in the drafts (human in the loop).

Every draft is reviewed by a doctor, who corrects it, describes each correction
(docs/benchmark/codebook.md) and approves it; after each visit the doctor fills in
out/<visit_id>/analysis.json (see run.py).

    python benchmark.py                          # the doctor approves with stageN.done files
    python benchmark.py --reviewer terminal      # the doctor reviews in the terminal (Enter)
    python benchmark.py --only visit_104         # selected scenarios (prefix match)
    python benchmark.py --summary-only           # summarize the outputs already in out/ (no model calls)
    python benchmark.py --summary-only --export results/v4_doctor   # tables (CSV) for the report

A scenario is a visit file in data/visits/ with a "benchmark" key: difficulty level,
features and hypotheses written before the run. Each scenario is run once.

There is no automatic scoring against a reference answer: the doctor's review is the
measurement. The summary counts what the doctor changed (log.json), every change with its
category, severity and source (from the doctor's notes) and who pointed it out (system note,
verifier, doctor), the doctor's assessment of each visit (analysis.json) and the hypotheses,
checked by hand after the run (out/benchmark/hypotheses_review.json).
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from run import OUT_DIR, REVIEWERS, run_visit
from src.drugs import registry_path
from src.review_format import (DOCUMENTS, NOTE_CATEGORIES, RATING_CRITERIA, SEVERITIES, SOURCES, analysis_gaps,
                               document_of, on_path)

ROOT = Path(__file__).parent
VISITS_DIR = ROOT / "data" / "visits"
RESULTS_DIR = OUT_DIR / "benchmark"
HYPOTHESES_REVIEW = RESULTS_DIR / "hypotheses_review.json"  # filled in after the run: {id: {confirmed, evidence}}
# doctor's notes re-classified after the run: [{visit_id, stage, change, reason}]
RECODING = RESULTS_DIR / "recoding.json"
# What the export keeps of each visit: both sides of each review, the log and the doctor's
# assessment. stageN_issues.json is in log.json; documentation.json is the approved stages
# plus the patient record.
EXPORTED_VISIT_FILES = ("stage1_draft.json", "stage1_approved.json", "stage2_draft.json", "stage2_approved.json",
                        "log.json", "analysis.json", "run_failures.json")

# --- scenarios -------------------------------------------------------------

def scenario_files() -> list[Path]:
    return [p for p in sorted(VISITS_DIR.glob("*.json")) if "benchmark" in json.loads(p.read_text(encoding="utf-8"))]


def load_scenarios() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in scenario_files()]


def scenarios_fingerprint() -> str:
    """SHA-256 of the scenario files: shows that scenarios and hypotheses did not change after the run."""
    digest = hashlib.sha256()
    for path in scenario_files():
        digest.update(path.name.encode() + path.read_bytes())
    return digest.hexdigest()


# --- the doctor's interventions --------------------------------------------

# The unit of the benchmark is the doctor's correction: one entry of doctor_notes, which
# describes one problem (docs/benchmark/codebook.md). A correction may edit several fields
# (switching a drug edits substance, strength, form, dosage, quantity and product).

def _parts(path: str) -> list[str]:
    return path.split(".")


def note_for(change: dict, notes: list[dict]) -> dict | None:
    """The note a changed field belongs to: the most specific note containing the field
    ('prescription.0.quantity' before 'prescription.0'), else any note on the same path."""
    parts = _parts(change["path"])
    containing = [n for n in notes if parts[:len(_parts(n["path"]))] == _parts(n["path"])]
    if containing:
        return max(containing, key=lambda n: len(_parts(n["path"])))
    return next((n for n in notes if on_path(change["path"], n["path"])), None)


def _detector(stage: int, pointed: bool) -> str:
    return ("uwaga systemu" if stage == 1 else "weryfikator") if pointed else "lekarz"


def _pointed(path: str, issues: list[dict]) -> bool:
    return any(on_path(path, issue["field"]) for issue in issues)


def annotated_changes(entry: dict) -> list[dict]:
    """Every changed field of one review with the classification of its correction."""
    rows = []
    for change in entry["changes"]:
        note = note_for(change, entry["doctor_notes"]) or {}
        rows.append({
            **change,
            "stage": entry["stage"],
            "document": document_of(change["path"]),
            "note_path": note.get("path"),
            "category": note.get("category"),
            "severity": note.get("severity"),
            "source": note.get("source"),
            "reason": note.get("reason"),
            "detected_by": _detector(entry["stage"], _pointed(change["path"], entry["issues"])),
        })
    return rows


def corrections(entry: dict) -> list[dict]:
    """One row per doctor's correction with the fields it changed. It counts as pointed out
    by the system when a system note concerned any of its fields."""
    rows = []
    for note in entry["doctor_notes"]:
        fields = [c for c in entry["changes"] if note_for(c, entry["doctor_notes"]) is note]
        paths = [c["path"] for c in fields] or [note["path"]]
        if len(fields) == 1:
            before, after = fields[0]["before"], fields[0]["after"]
        else:
            # several fields: {field path relative to the note: value}
            relative = lambda c: c["path"][len(note["path"]) + 1:] or c["path"]
            before = {relative(c): c["before"] for c in fields}
            after = {relative(c): c["after"] for c in fields}
        rows.append({
            "stage": entry["stage"],
            "document": document_of(note["path"]),
            "path": note["path"],
            "category": note.get("category"),
            "severity": note.get("severity"),
            "source": note.get("source"),
            "detected_by": _detector(entry["stage"], any(_pointed(p, entry["issues"]) for p in paths)),
            "n_fields": len(fields),
            "chars_edited": sum(c.get("chars_edited", 0) for c in fields),
            "before": before,
            "after": after,
            "reason": note.get("reason"),
        })
    return rows


def interventions(log: list[dict]) -> dict:
    stages = {}
    for entry in log:
        changes, fixes, issues = annotated_changes(entry), corrections(entry), entry["issues"]
        stages[f"stage{entry['stage']}"] = {
            "accepted_unchanged": entry["accepted_unchanged"],
            "corrections": len(fixes),
            "fields_changed": len(changes),
            "by_kind": dict(Counter(c["kind"] for c in changes)),
            "by_document": dict(Counter(c["document"] for c in fixes)),
            "chars_edited": entry["chars_edited"],
            "doc_chars": entry["doc_chars"],
            "product_choices": sum(c["path"].endswith(".product") for c in changes),
            "pointed_by_issue": sum(c["detected_by"] != "lekarz" for c in fixes),
            "fields_without_note": sum(c["note_path"] is None for c in changes),
            "issues": len(issues),
            # system notes the doctor did not act on (not a problem, or a false alarm)
            "issues_without_change": sum(not any(on_path(c["path"], i["field"]) for c in changes) for i in issues),
        }
    return stages


# --- running ---------------------------------------------------------------

def run_recording_failure(visit: dict, reviewer: str) -> None:
    """A failed run is a result too: it is recorded in out/<visit_id>/run_failures.json
    (written only after a failure) and the benchmark goes on with the next visit."""
    out_dir = OUT_DIR / visit["visit_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "documentation.json").unlink(missing_ok=True)
    failures_path = out_dir / "run_failures.json"
    failures_path.unlink(missing_ok=True)
    try:
        run_visit(visit, reviewer)
    except Exception as error:  # noqa: BLE001 - any failure of the system is recorded
        message = f"{type(error).__name__}: {' '.join(str(error).split())[:400]}"
        print(f"  run failed: {message}", flush=True)
        failures_path.write_text(json.dumps([{"error": message}], ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def build_case(visit: dict) -> dict | None:
    out_dir = OUT_DIR / visit["visit_id"]
    if not (out_dir / "documentation.json").exists():
        return None
    log = read_json(out_dir / "log.json", [])
    b = visit["benchmark"]
    analysis = read_json(out_dir / "analysis.json", {})
    return {
        "visit_id": visit["visit_id"],
        "title": b["title"],
        "short_title": b.get("short_title", b["title"]),
        "difficulty": b.get("difficulty"),
        "specialty": b.get("specialty"),
        "features": b.get("features", []),
        "words": {"stage1": len(visit["stage1_dictation"].split()), "stage2": len(visit["stage2_dictation"].split())},
        "reviewer": log[0]["reviewer"] if log else None,
        # items on the prescription the doctor approved (descriptive, not a score)
        "drugs_prescribed": len(read_json(out_dir / "documentation.json")["visit_note"]["prescribed_drugs"]["items"]),
        "interventions": interventions(log),
        "corrections": [c for entry in log for c in corrections(entry)],
        "changes": [c for entry in log for c in annotated_changes(entry)],
        "issues": [{**i, "stage": e["stage"],
                    "acted_on": any(on_path(c["path"], i["field"]) for c in e["changes"])} for e in log for i in e["issues"]],
        "analysis": analysis,
        "analysis_gaps": analysis_gaps(analysis),
        "run_failures": read_json(out_dir / "run_failures.json", []),
    }


# --- summaries -------------------------------------------------------------

def fraction(found: int, total: int) -> str:
    return f"{found}/{total}" if total else "—"


def totals(cases: list[dict]) -> dict:
    reviews = [stage for c in cases for stage in c["interventions"].values()]
    fixes = [x for c in cases for x in c["corrections"]]
    changes = [ch for c in cases for ch in c["changes"]]
    return {
        "interventions": {
            "drafts": len(reviews),
            "drafts_accepted_unchanged": sum(r["accepted_unchanged"] for r in reviews),
            "corrections": len(fixes),
            "by_document": {d: sum(x["document"] == d for x in fixes) for d in DOCUMENTS},
            "by_source": {s: sum(x["source"] == s for x in fixes) for s in SOURCES},
            "by_detection": dict(Counter(x["detected_by"] for x in fixes)),
            "category_by_severity": {cat: {s: sum(x["category"] == cat and x["severity"] == s for x in fixes)
                                           for s in SEVERITIES} for cat in NOTE_CATEGORIES},
            "fields_changed": len(changes),
            "fields_by_kind": dict(Counter(c["kind"] for c in changes)),
            "fields_without_note": sum(r["fields_without_note"] for r in reviews),
            "chars_edited": sum(r["chars_edited"] for r in reviews),
            "doc_chars": sum(r["doc_chars"] for r in reviews),
            "product_choices": sum(r["product_choices"] for r in reviews),
            "issues": sum(r["issues"] for r in reviews),
            "issues_without_change": sum(r["issues_without_change"] for r in reviews),
        },
        "by_difficulty": {
            level: {"visits": len(group),
                    "drafts_unchanged": fraction(sum(r["accepted_unchanged"] for c in group for r in c["interventions"].values()),
                                                 2 * len(group)),
                    "corrections": sum(len(c["corrections"]) for c in group),
                    "significant": sum(x["severity"] == "istotny" for c in group for x in c["corrections"]),
                    "system_caused": sum(x["source"] == "system" for c in group for x in c["corrections"]),
                    "fields_changed": sum(len(c["changes"]) for c in group)}
            for level in sorted({c["difficulty"] for c in cases})
            for group in [[c for c in cases if c["difficulty"] == level]]},
        "verdicts": dict(Counter(c["analysis"].get("verdict") for c in cases)),
        "failed_runs": sum(len(c["run_failures"]) for c in cases),
    }


def print_report(cases: list[dict]) -> None:
    print(f"\n{'visit':<11}{'lvl':>4}{'drugs':>6}{'corr 1/2':>10}{'fields':>7}{'sys/dyk/rej':>13}{'i/u/k':>8}  verdict")
    for case in cases:
        r, fx = case["interventions"], case["corrections"]
        print(f"{case['visit_id']:<11}{case['difficulty'] or '':>4}{case['drugs_prescribed']:>6}"
              f"{r.get('stage1', {}).get('corrections', '—')!s:>6}/{r.get('stage2', {}).get('corrections', '—')!s:<3}"
              f"{len(case['changes']):>7}"
              f"{'/'.join(str(sum(x['source'] == s for x in fx)) for s in SOURCES):>13}"
              f"{'/'.join(str(sum(x['severity'] == s for x in fx)) for s in SEVERITIES):>8}"
              f"  {case['analysis'].get('verdict') or '(brak opisu)'}")
        if case["analysis_gaps"]:
            print(f"{'':<4}analysis.json niekompletny: {', '.join(case['analysis_gaps'])}")
    t = totals(cases)
    i = t["interventions"]
    print(f"\ninterventions: {i['drafts_accepted_unchanged']}/{i['drafts']} drafts accepted unchanged, "
          f"{i['corrections']} corrections, by document {i['by_document']}, by source {i['by_source']}, "
          f"detected by {i['by_detection']}; {i['fields_changed']} fields changed, "
          f"{i['chars_edited']}/{i['doc_chars']} chars edited, {i['product_choices']} products chosen, "
          f"{i['issues_without_change']}/{i['issues']} system notes without a change, "
          f"{i['fields_without_note']} changed fields without a note")
    for category, by_severity in i["category_by_severity"].items():
        if any(by_severity.values()):
            print(f"  {category:<24}" + "  ".join(f"{s}={n}" for s, n in by_severity.items()))
    for level, row in t["by_difficulty"].items():
        print(f"  level {level}: " + ", ".join(f"{k}={v}" for k, v in row.items()))
    print(f"verdicts: {t['verdicts']}")
    if t["failed_runs"]:
        print(f"failed runs: {t['failed_runs']}")


# --- export for the report -------------------------------------------------

def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def export(cases: list[dict], scenarios: list[dict], target: Path, results: dict) -> None:
    """Tidy CSV tables (one row per visit / correction / changed field / system note / feature /
    hypothesis) and the raw outputs of every visit, for LaTeX (pgfplotstable, csvsimple)."""
    target.mkdir(parents=True, exist_ok=True)
    (target / "run.json").write_text(json.dumps({k: v for k, v in results.items() if k != "cases"},
                                                ensure_ascii=False, indent=2), encoding="utf-8")
    flat = lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value

    visits = []
    for c in cases:
        fx, a = c["corrections"], c["analysis"]
        visits.append({
            "visit_id": c["visit_id"], "short_title": c["short_title"], "difficulty": c["difficulty"],
            "specialty": c["specialty"], "features": ";".join(c["features"]), "n_features": len(c["features"]),
            "drugs_prescribed": c["drugs_prescribed"], "words_stage1": c["words"]["stage1"],
            "words_stage2": c["words"]["stage2"],
            "stage1_unchanged": c["interventions"].get("stage1", {}).get("accepted_unchanged"),
            "stage2_unchanged": c["interventions"].get("stage2", {}).get("accepted_unchanged"),
            "corrections": len(fx), "corrections_stage1": sum(x["stage"] == 1 for x in fx),
            "corrections_stage2": sum(x["stage"] == 2 for x in fx),
            **{f"severity_{s}": sum(x["severity"] == s for x in fx) for s in SEVERITIES},
            **{f"source_{s}": sum(x["source"] == s for x in fx) for s in SOURCES},
            "detected_by_system": sum(x["detected_by"] != "lekarz" for x in fx),
            "fields_changed": len(c["changes"]),
            "chars_edited": sum(r["chars_edited"] for r in c["interventions"].values()),
            "doc_chars": sum(r["doc_chars"] for r in c["interventions"].values()),
            "verdict": a.get("verdict"),
            **{f"rating_{k}": (a.get("ratings") or {}).get(k) for k in RATING_CRITERIA},
            "summary": a.get("summary"),
        })
    write_csv(target / "visits.csv", visits, list(visits[0]) if visits else [])

    rows = lambda key: [{**{k: flat(v) for k, v in x.items()}, "visit_id": c["visit_id"], "difficulty": c["difficulty"]}
                        for c in cases for x in c[key]]
    write_csv(target / "corrections.csv", rows("corrections"),
              ["visit_id", "difficulty", "stage", "document", "path", "category", "severity", "source", "detected_by",
               "n_fields", "chars_edited", "before", "after", "reason"])
    write_csv(target / "field_changes.csv", rows("changes"),
              ["visit_id", "difficulty", "stage", "document", "path", "kind", "note_path", "category", "severity",
               "source", "detected_by", "chars_edited", "before", "after"])
    write_csv(target / "issues.csv",
              [{"visit_id": c["visit_id"], "stage": i["stage"], "field": i["field"], "problem": i["problem"],
                "acted_on": i["acted_on"]} for c in cases for i in c["issues"]],
              ["visit_id", "stage", "field", "problem", "acted_on"])
    write_csv(target / "features.csv",
              [{"visit_id": c["visit_id"], "difficulty": c["difficulty"], "feature": f,
                "corrections": len(c["corrections"]),
                "corrections_system": sum(x["source"] == "system" for x in c["corrections"])}
               for c in cases for f in c["features"]],
              ["visit_id", "difficulty", "feature", "corrections", "corrections_system"])
    review = read_json(HYPOTHESES_REVIEW, {})
    write_csv(target / "hypotheses.csv",
              [{"id": h["id"], "visit_id": s["visit_id"], "text": h["text"],
                "confirmed": review.get(h["id"], {}).get("confirmed"), "evidence": review.get(h["id"], {}).get("evidence")}
               for s in scenarios for h in s["benchmark"].get("hypotheses", [])],
              ["id", "visit_id", "text", "confirmed", "evidence"])
    for c in cases:
        visit_dir = target / "visits" / c["visit_id"]
        visit_dir.mkdir(parents=True, exist_ok=True)
        for name in EXPORTED_VISIT_FILES:
            if (OUT_DIR / c["visit_id"] / name).exists():
                shutil.copy2(OUT_DIR / c["visit_id"] / name, visit_dir / name)
    print(f"Exported: {target}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reviewer", choices=REVIEWERS, default="files",
                        help="files: the doctor approves with stageN.done files; terminal: with Enter (see run.py)")
    parser.add_argument("--only", nargs="+", metavar="VISIT_ID", help="run only these scenarios (prefix match)")
    parser.add_argument("--summary-only", action="store_true", help="summarize outputs already in out/, no model calls")
    parser.add_argument("--export", type=Path, metavar="DIR", help="write CSV tables and visit outputs for the report")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    scenarios = load_scenarios()
    if args.only:
        scenarios = [s for s in scenarios if any(s["visit_id"].startswith(o) for o in args.only)]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if not args.summary_only:
        (RESULTS_DIR / "run_started.json").write_text(json.dumps({
            "date": date.today().isoformat(), "scenarios_sha256": scenarios_fingerprint(),
            "visits": [s["visit_id"] for s in scenarios]}, indent=2), encoding="utf-8")
        RECODING.unlink(missing_ok=True)  # belongs to the previous run
        for visit in scenarios:
            print(f"\n##### {visit['visit_id']}: {visit['benchmark']['title']}", flush=True)
            run_recording_failure(visit, args.reviewer)

    cases = [c for c in (build_case(v) for v in scenarios) if c]
    started = read_json(RESULTS_DIR / "run_started.json", {})
    results = {
        "date": date.today().isoformat(),
        "run_started": started.get("date"),
        "model": os.environ.get("LLM_MODEL"),
        "drug_registry": registry_path().name,
        "reviewer": args.reviewer,
        "scenarios_sha256_at_run": started.get("scenarios_sha256"),
        # set when the scenario files were edited after the run in a documented way
        **{k: started[k] for k in ("scenarios_sha256_with_reference_answers", "note") if k in started},
        "scenarios_sha256_now": scenarios_fingerprint(),
        "recoding": read_json(RECODING, []),
        "totals": totals(cases),
        "cases": cases,
    }
    results_path = RESULTS_DIR / f"results_{args.reviewer}.json"
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(cases)
    print(f"\nResults: {results_path}")
    if args.export:
        export(cases, scenarios, args.export, results)


if __name__ == "__main__":
    main()
