"""LaTeX fragments for the benchmark report, built from an exported benchmark run.

    python benchmark_report.py results/v4_doctor docs/benchmark/generated

Reads the files written by `benchmark.py --export` (CSV tables, run.json, visits/*/analysis.json)
and the scenario files in data/visits/, and writes the tables, the chart data and the per-visit
descriptions that docs/benchmark/benchmark.tex includes. Every number in the report comes from
these fragments, so the report can be rebuilt from the repository alone.
"""

import argparse
import csv
import json
import statistics
from collections import Counter
from pathlib import Path

from src.review_format import NOTE_CATEGORIES, RATING_CRITERIA, SEVERITIES, SOURCES

ROOT = Path(__file__).parent
VISITS_DIR = ROOT / "data" / "visits"
LEVELS = ("A", "B", "C")
DETECTORS = ("uwaga systemu", "weryfikator", "lekarz")
STAGE_NAMES = {"1": "recepta i zalecenia", "2": "karta wizyty"}

# characters pdflatex (utf8 inputenc) cannot typeset directly
REPLACEMENTS = {"≥": r"$\geq$", "≤": r"$\leq$", "→": r"$\rightarrow$", "×": r"$\times$", " ": "~"}
SPECIAL = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&", "#": r"\#",
           "^": r"\^{}", "_": r"\_", "%": r"\%", "~": r"\textasciitilde{}"}


def tex(value) -> str:
    """Plain text -> LaTeX (special characters escaped)."""
    text = "".join(SPECIAL.get(ch, ch) for ch in str(value))
    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)
    return text


def number(value: float, digits: int = 1) -> str:
    """Polish decimal comma."""
    return f"{value:.{digits}f}".replace(".", ",")


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load(results: Path) -> dict:
    visits = read_csv(results / "visits.csv")
    scenarios = {}
    for path in sorted(VISITS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "benchmark" in data:
            scenarios[data["visit_id"]] = data["benchmark"]
    return {
        "run": json.loads((results / "run.json").read_text(encoding="utf-8")),
        "visits": visits,
        "corrections": read_csv(results / "corrections.csv"),
        "fields": read_csv(results / "field_changes.csv"),
        "issues": read_csv(results / "issues.csv"),
        "features": read_csv(results / "features.csv"),
        "hypotheses": read_csv(results / "hypotheses.csv"),
        "analysis": {v["visit_id"]: json.loads((results / "visits" / v["visit_id"] / "analysis.json")
                                                .read_text(encoding="utf-8")) for v in visits},
        "scenarios": scenarios,
    }


# --- fragments ---------------------------------------------------------------

def macros(d: dict) -> str:
    """Key numbers as \\newcommand macros, used in the text of the report."""
    visits, fixes, issues, hyps = d["visits"], d["corrections"], d["issues"], d["hypotheses"]
    totals = d["run"]["totals"]["interventions"]
    unchanged = sum((v["stage1_unchanged"] == "True") + (v["stage2_unchanged"] == "True") for v in visits)
    stage2_system = [x for x in fixes if x["stage"] == "2" and x["source"] == "system"]
    values = {
        "nVisits": len(visits),
        "nDrafts": 2 * len(visits),
        "nDraftsUnchanged": unchanged,
        "nCorrections": len(fixes),
        "nFieldsChanged": len(d["fields"]),
        "nCharsEdited": totals["chars_edited"],
        "nDocChars": totals["doc_chars"],
        "pCharsEdited": number(100 * totals["chars_edited"] / totals["doc_chars"]),
        "nProductChoices": totals["product_choices"],
        **{f"nSev{key}": sum(x["severity"] == s for x in fixes)
           for key, s in zip(("Significant", "Moderate", "Cosmetic"), SEVERITIES)},
        **{f"nSrc{key}": sum(x["source"] == s for x in fixes)
           for key, s in zip(("System", "Dictation", "Registry"), SOURCES)},
        **{f"nDet{key}": sum(x["detected_by"] == s for x in fixes)
           for key, s in zip(("SystemNote", "Verifier", "Doctor"), DETECTORS)},
        "nVisitsSystemModerate": len({x["visit_id"] for x in fixes
                                      if x["source"] == "system" and x["severity"] != "kosmetyczny"}),
        "nIssuesStageOne": sum(i["stage"] == "1" for i in issues),
        "nIssuesStageOneActed": sum(i["stage"] == "1" and i["acted_on"] == "True" for i in issues),
        "nIssuesStageTwo": sum(i["stage"] == "2" for i in issues),
        "nIssuesStageTwoActed": sum(i["stage"] == "2" and i["acted_on"] == "True" for i in issues),
        "nStageTwoSystem": len(stage2_system),
        "nStageTwoSystemByVerifier": sum(x["detected_by"] == "weryfikator" for x in stage2_system),
        "nHypotheses": len(hyps),
        "nHypothesesConfirmed": sum(h["confirmed"] == "True" for h in hyps),
        "runModel": tex(d["run"]["model"]),
        "runRegistry": tex(d["run"]["drug_registry"]),
        "runDate": tex(d["run"]["run_started"]),
        "runFingerprint": tex(d["run"]["scenarios_sha256_at_run"][:16]),
    }
    return "\n".join(f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in values.items()) + "\n"


def scenarios_table(d: dict) -> str:
    rows = []
    for v in d["visits"]:
        b = d["scenarios"][v["visit_id"]]
        rows.append(f"{v['visit_id'][-3:]} & {v['difficulty']} & {tex(b['title'])} & {tex(b['specialty'])} & "
                    f"\\footnotesize {tex(', '.join(b['features']))} \\\\")
    return "\n".join(rows) + "\n"


def levels_table(d: dict) -> str:
    rows = []
    for level in (*LEVELS, None):
        vs = [v for v in d["visits"] if level is None or v["difficulty"] == level]
        fx = [x for x in d["corrections"] if level is None or x["difficulty"] == level]
        unchanged = sum((v["stage1_unchanged"] == "True") + (v["stage2_unchanged"] == "True") for v in vs)
        system = sum(x["source"] == "system" for x in fx)
        name = level or "\\textbf{razem}"
        rows.append(f"{name} & {len(vs)} & {unchanged}/{2 * len(vs)} & {len(fx)} & {number(len(fx) / len(vs), 2)} & "
                    f"{system} & {number(system / len(vs), 2)} & {sum(x['severity'] == 'istotny' for x in fx)} & "
                    f"{sum(int(v['fields_changed']) for v in vs)} \\\\")
        if level == LEVELS[-1]:
            rows.append("\\midrule")
    return "\n".join(rows) + "\n"


def level_chart(d: dict) -> str:
    """pgfplots: corrections per visit at each level, stacked by source."""
    plots = []
    for source in SOURCES:
        coords = " ".join(
            f"({level},{len([x for x in d['corrections'] if x['difficulty'] == level and x['source'] == source]) / n:.3f})"
            for level in LEVELS for n in [sum(v["difficulty"] == level for v in d["visits"])])
        plots.append(f"\\addplot coordinates {{{coords}}};")
    return "\n".join(plots) + "\n\\legend{" + ",".join(SOURCES) + "}\n"


def category_table(d: dict) -> str:
    fixes = d["corrections"]
    rows = []
    for category in NOTE_CATEGORIES:
        counts = [sum(x["category"] == category and x["severity"] == s for x in fixes) for s in SEVERITIES]
        rows.append(f"{tex(category)} & " + " & ".join(map(str, counts)) + f" & {sum(counts)} \\\\")
    totals = [sum(x["severity"] == s for x in fixes) for s in SEVERITIES]
    rows += ["\\midrule", "\\textbf{razem} & " + " & ".join(map(str, totals)) + f" & {len(fixes)} \\\\"]
    return "\n".join(rows) + "\n"


def source_table(d: dict) -> str:
    fixes = d["corrections"]
    rows = []
    for source in SOURCES:
        counts = [sum(x["source"] == source and x["detected_by"] == det for x in fixes) for det in DETECTORS]
        rows.append(f"{tex(source)} & " + " & ".join(map(str, counts)) + f" & {sum(counts)} \\\\")
    totals = [sum(x["detected_by"] == det for x in fixes) for det in DETECTORS]
    rows += ["\\midrule", "\\textbf{razem} & " + " & ".join(map(str, totals)) + f" & {len(fixes)} \\\\"]
    return "\n".join(rows) + "\n"


def features_table(d: dict) -> str:
    by_feature: dict[str, list[dict]] = {}
    for row in d["features"]:
        by_feature.setdefault(row["feature"], []).append(row)
    ranked = sorted(by_feature.items(), key=lambda kv: (-statistics.mean(int(r["corrections_system"]) for r in kv[1]),
                                                        -len(kv[1]), kv[0]))
    rows = [f"{tex(feature)} & {len(rs)} & {number(statistics.mean(int(r['corrections']) for r in rs), 2)} & "
            f"{number(statistics.mean(int(r['corrections_system']) for r in rs), 2)} \\\\" for feature, rs in ranked]
    return "\n".join(rows) + "\n"


def ratings_table(d: dict) -> str:
    rows = []
    for level in (*LEVELS, None):
        vs = [v for v in d["visits"] if level is None or v["difficulty"] == level]
        means = [number(statistics.mean(int(v[f"rating_{c}"]) for v in vs), 2) for c in RATING_CRITERIA]
        verdicts = Counter(v["verdict"] for v in vs)
        if level is None:
            rows.append("\\midrule")
        rows.append(f"{level or chr(92) + 'textbf{razem}'} & " + " & ".join(means) + " & " +
                    " & ".join(str(verdicts.get(x, 0)) for x in
                               ("gotowy bez zmian", "drobne poprawki", "istotne poprawki", "nie do użycia")) + " \\\\")
    return "\n".join(rows) + "\n"


def hypotheses_table(d: dict) -> str:
    rows = []
    for h in d["hypotheses"]:
        result = "tak" if h["confirmed"] == "True" else "\\textbf{nie}" if h["confirmed"] == "False" else "--"
        rows.append(f"{tex(h['id'])} & {tex(h['text'])} & {result} & \\footnotesize {tex(h['evidence'] or '')} \\\\")
    return "\n".join(rows) + "\n"


def visits_sections(d: dict) -> str:
    """One subsection per visit: scenario, the reviewer's description, every correction."""
    out = []
    for v in d["visits"]:
        vid, b, a = v["visit_id"], d["scenarios"][v["visit_id"]], d["analysis"][v["visit_id"]]
        fixes = [x for x in d["corrections"] if x["visit_id"] == vid]
        ratings = ", ".join(f"{tex(c)} {a['ratings'][c]}" for c in RATING_CRITERIA)
        heading = f"{vid[-3:]}. {tex(b['title'])}"
        out += [
            # unnumbered: the visit number is the heading's number (\wizyta{106} in the text links here)
            f"\\subsection*{{{heading}}}\\phantomsection\\addcontentsline{{toc}}{{subsection}}{{{heading}}}"
            f"\\label{{wizyta:{vid[-3:]}}}",
            f"\\textbf{{Poziom}} {v['difficulty']} · {tex(b['specialty'])} · "
            f"\\textbf{{cechy:}} {tex(', '.join(b['features']))}\\par",
            f"\\textit{{Scenariusz.}} {tex(b['clinical_summary'])}\\par",
            f"\\textit{{Ocena recenzenta.}} {tex(a['summary'])} "
            f"Werdykt: \\textbf{{{tex(a['verdict'])}}}; oceny 1--5: {ratings}.\\par",
        ]
        for label, items in (("Mocne strony", a["strengths"]), ("Braki", a["weaknesses"])):
            if items:
                out.append(f"\\textit{{{label}:}}")
                out.append("\\begin{itemize}[nosep]")
                out += [f"\\item {tex(item)}" for item in items]
                out.append("\\end{itemize}")
        if fixes:
            out.append("\\textit{Poprawki lekarza:}")
            out.append("\\begin{itemize}[nosep]")
            for x in fixes:
                out.append(f"\\item etap {x['stage']} ({STAGE_NAMES[x['stage']]}), \\texttt{{{tex(x['path'])}}}: "
                           f"{tex(x['category'])} / {tex(x['severity'])} / źródło: {tex(x['source'])} / "
                           f"wykrył: {tex(x['detected_by'])}. {tex(x['reason'])}")
            out.append("\\end{itemize}")
        else:
            out.append("\\textit{Poprawki lekarza:} brak – oba projekty zatwierdzone bez zmian.\\par")
        out.append("")
    return "\n".join(out) + "\n"


FRAGMENTS = {
    "macros.tex": macros,
    "scenarios.tex": scenarios_table,
    "levels.tex": levels_table,
    "level_chart.tex": level_chart,
    "categories.tex": category_table,
    "sources.tex": source_table,
    "features.tex": features_table,
    "ratings.tex": ratings_table,
    "hypotheses.tex": hypotheses_table,
    "visits.tex": visits_sections,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path, help="directory written by benchmark.py --export")
    parser.add_argument("target", type=Path, help="directory for the LaTeX fragments")
    args = parser.parse_args()
    data = load(args.results)
    args.target.mkdir(parents=True, exist_ok=True)
    for name, build in FRAGMENTS.items():
        (args.target / name).write_text("% generated by benchmark_report.py - do not edit\n" + build(data),
                                        encoding="utf-8")
    print(f"LaTeX fragments: {args.target}")


if __name__ == "__main__":
    main()
