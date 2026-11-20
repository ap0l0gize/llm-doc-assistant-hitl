"""Run a visit without a frontend: the doctor reviews each draft as a JSON file.

    python run.py data/visits/visit_001.json                  # the doctor reviews in the terminal
    python run.py data/visits/visit_001.json --editor code    # ... and each draft opens in VS Code
    python run.py data/visits/visit_001.json --doctor-files   # the doctor signals "done" with a file

At each review point the draft is written to out/<visit_id>/stageN_draft.json and
the system's notes to stageN_issues.json. The doctor corrects the draft, saves it and
approves it: Enter in the terminal, or (--doctor-files) by creating stageN.done next
to the draft. The visit then moves on to the next stage. A document that cannot be
approved (invalid JSON, a missing quantity, an unknown product) is reported and the
doctor corrects it again.

After approval the doctor's version is saved to stageN_approved.json and stageN_draft.json
gets back what the system produced, so each stage keeps both sides of the review
(log.json lists the changes between them).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from langgraph.types import Command

from src.graph import build_graph
from src.review_format import (ReviewError, analysis_template, chars_edited, diff, from_editable, has_note,
                               pointed_by_issue, summary, text_chars, to_editable)

OUT_DIR = Path(__file__).parent / "out"
REVIEWERS = ("terminal", "files")
STAGE_NAMES = {1: "recepta i zalecenia", 2: "karta wizyty"}
DONE_POLL_S = 1.0
DONE_TIMEOUT_S = 2 * 60 * 60  # an abandoned review should not keep the process alive forever


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _say(*lines: str) -> None:
    print(*lines, sep="\n", flush=True)


def _dictation_text(visit: dict, stage: int) -> str:
    if f"stage{stage}_dictation" in visit:
        return visit[f"stage{stage}_dictation"]
    from src.stt import transcribe

    return transcribe(visit[f"stage{stage}_audio"])


def _open_in_editor(editor: str, path: Path) -> None:
    subprocess.Popen([shutil.which(editor) or editor, str(path)])


def _wait_for_doctor(stage: int, out_dir: Path, reviewer: str) -> None:
    if reviewer == "terminal":
        input("Popraw i zapisz plik, potem Enter = zatwierdź i przejdź dalej: ")
        return
    done = out_dir / f"stage{stage}.done"
    deadline = time.monotonic() + DONE_TIMEOUT_S
    while not done.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"No {done.name} within {DONE_TIMEOUT_S} s")
        time.sleep(DONE_POLL_S)
    done.unlink()


def _review(task: dict, visit_id: str, out_dir: Path, reviewer: str, editor: str | None, log: list) -> dict:
    stage, issues = task["stage"], task["issues"]
    draft = to_editable(stage, task["documents"])
    draft_path = out_dir / f"stage{stage}_draft.json"
    _write_json(draft_path, draft)
    _write_json(out_dir / f"stage{stage}_issues.json", issues)

    _say(f"\n=== {visit_id} · Etap {stage} ({STAGE_NAMES[stage]}) ===", *summary(stage, draft, issues),
         f"Projekt do poprawy: {draft_path}")
    if reviewer == "files":
        _say(f"AWAITING REVIEW {visit_id} stage {stage} {draft_path}")
    elif editor:
        _open_in_editor(editor, draft_path)
    error_path = out_dir / f"stage{stage}_error.txt"
    while True:
        _wait_for_doctor(stage, out_dir, reviewer)
        try:
            approved = json.loads(draft_path.read_text(encoding="utf-8"))
            documents, notes = from_editable(stage, approved)
            break
        except json.JSONDecodeError as error:
            message = f"niepoprawny JSON: {error}"
        except ReviewError as error:
            message = str(error)
        _say(f"! Nie można zatwierdzić: {message}")
        if reviewer == "files":
            error_path.write_text(message, encoding="utf-8")
            _say(f"INVALID {visit_id} stage {stage}: {message}")
    error_path.unlink(missing_ok=True)

    changes = diff(draft, approved)
    for change in changes:
        change["chars_edited"] = chars_edited(change)
        change["pointed_by_issue"] = pointed_by_issue(change, issues)
        change["has_note"] = has_note(change, notes)
    log.append({
        "stage": stage,
        "reviewer": reviewer,
        "accepted_unchanged": not changes,
        "changes": changes,
        "chars_edited": sum(c["chars_edited"] for c in changes),
        "doc_chars": text_chars(draft),  # text the doctor had to read
        "issues": issues,
        "doctor_notes": notes,
    })
    _write_json(out_dir / f"stage{stage}_approved.json", approved)
    _write_json(draft_path, draft)  # the doctor edited the draft in place: keep what the system produced
    if reviewer == "files":
        _say(f"APPROVED {visit_id} stage {stage} ({len(changes)} changes)")
    return documents


def run_visit(visit: dict, reviewer: str = "terminal", editor: str | None = None) -> tuple[dict, list[dict]]:
    """Runs one visit through both stages with the given reviewer (terminal | files);
    returns the final documentation and the review log. Files go to out/<visit_id>/."""
    visit_id = visit["visit_id"]
    out_dir = OUT_DIR / visit_id
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in [*out_dir.glob("stage*"), *(out_dir / name for name in ("documentation.json", "log.json", "analysis.json"))]:
        stale.unlink(missing_ok=True)  # a leftover stageN.done would approve the new draft at once
    log: list[dict] = []

    graph = build_graph()
    config = {"configurable": {"thread_id": visit_id}}
    result = graph.invoke(
        {"visit_id": visit_id, "pesel": visit["pesel"], "stage1_dictation": _dictation_text(visit, 1)},
        config,
    )
    while "__interrupt__" in result:
        task = result["__interrupt__"][0].value
        if task["type"] == "dictation":
            response = _dictation_text(visit, task["stage"])
        else:
            response = _review(task, visit_id, out_dir, reviewer, editor, log)
        result = graph.invoke(Command(resume=response), config)

    _write_json(out_dir / "documentation.json", result["documentation"])
    _write_json(out_dir / "log.json", log)
    # The reviewer's description of the visit (docs/benchmark/codebook.md, section 5).
    _write_json(out_dir / "analysis.json", analysis_template(visit_id))
    if reviewer == "files":
        _say(f"DONE {visit_id} (opis wizyty: {out_dir / 'analysis.json'})")
    return result["documentation"], log


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("visit", type=Path, help="JSON file with the visit input")
    parser.add_argument("--doctor-files", action="store_true",
                        help="instead of Enter, wait for out/<visit_id>/stageN.done (e.g. a simulated doctor)")
    parser.add_argument("--editor", default=os.environ.get("EDITOR"),
                        help="open each draft in this editor, e.g. 'code' (default: $EDITOR)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # Polish text on a Windows console

    visit = json.loads(args.visit.read_text(encoding="utf-8"))
    run_visit(visit, "files" if args.doctor_files else "terminal", args.editor)
    _say(f"\nGotowe: {OUT_DIR / visit['visit_id'] / 'documentation.json'} (przegląd: log.json)")


if __name__ == "__main__":
    main()
