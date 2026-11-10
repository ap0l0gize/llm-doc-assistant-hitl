"""Run a visit without a frontend: the doctor "approves" by editing JSON files.

    python run.py data/visits/visit_001.json          # interactive
    python run.py data/visits/visit_001.json --auto   # approve drafts unchanged

At every approval point the draft is written to out/<visit_id>/stageN_draft.json.
Edit the file and press Enter to approve it, or type a comment to send the
documents back to the agents for revision.
"""

import argparse
import difflib
import json
import time
from pathlib import Path

from langgraph.types import Command

from src.graph import build_graph

OUT_DIR = Path(__file__).parent / "out"


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _dictation_text(visit: dict, stage: int) -> str:
    if f"stage{stage}_dictation" in visit:
        return visit[f"stage{stage}_dictation"]
    from src.stt import transcribe

    return transcribe(visit[f"stage{stage}_audio"])


def _review(task: dict, out_dir: Path, auto: bool, log: list) -> dict:
    stage = task["stage"]
    draft_path = out_dir / f"stage{stage}_draft.json"
    _write_json(draft_path, task["documents"])
    print(f"\n=== Stage {stage}: draft awaiting approval -> {draft_path}")
    for issue in task["issues"]:
        print(f"  ! verifier [{issue['field']}]: {issue['problem']}")

    start = time.time()
    comment = "" if auto else input("Enter = approve (with your edits to the file) | text = send back for revision: ").strip()
    entry = {"stage": stage, "review_time_s": round(time.time() - start, 1)}
    if comment:
        log.append({**entry, "decision": "sent_back", "comment": comment})
        return {"approved": False, "comment": comment}

    approved = json.loads(draft_path.read_text(encoding="utf-8"))
    draft, final = (json.dumps(d, ensure_ascii=False, sort_keys=True) for d in (task["documents"], approved))
    log.append({
        **entry,
        "decision": "approved",
        # 1.0 = the doctor changed nothing; the lower, the more edits
        "draft_final_similarity": round(difflib.SequenceMatcher(None, draft, final).ratio(), 3),
        "verifier_issues": len(task["issues"]),
    })
    _write_json(out_dir / f"stage{stage}_approved.json", approved)
    return {"approved": True, "documents": approved}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("visit", type=Path, help="JSON file with the visit input")
    parser.add_argument("--auto", action="store_true", help="approve drafts without waiting for the doctor")
    args = parser.parse_args()

    visit = json.loads(args.visit.read_text(encoding="utf-8"))
    out_dir = OUT_DIR / visit["visit_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    log: list[dict] = []

    graph = build_graph()
    config = {"configurable": {"thread_id": visit["visit_id"]}}
    result = graph.invoke(
        {"visit_id": visit["visit_id"], "pesel": visit["pesel"],
         "stage1_dictation": _dictation_text(visit, 1)},
        config,
    )
    while "__interrupt__" in result:
        task = result["__interrupt__"][0].value
        if task["type"] == "dictation":
            response = _dictation_text(visit, task["stage"])
        else:
            response = _review(task, out_dir, args.auto, log)
        result = graph.invoke(Command(resume=response), config)

    _write_json(out_dir / "documentation.json", result["documentation"])
    _write_json(out_dir / "log.json", log)
    print(f"\nDone: {out_dir / 'documentation.json'} (metrics: log.json)")


if __name__ == "__main__":
    main()
