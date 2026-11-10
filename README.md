# llm-doc-assistant-hitl

Prototype of a medical documentation assistant. An LLM drafts prescriptions, patient instructions and visit notes from the doctor's dictation; the doctor reviews, edits and approves every document before it is saved.

Research project for the *Scientific Research* (Badania Naukowe) course. The question we study: how much of a doctor's administrative work can be sped up by a human-in-the-loop LLM workflow, without handing any medical decisions to the system.

> **Status:** proof of concept. All patient data is synthetic. Not for clinical use.

## How it works

A visit has two stages, each ending with the doctor's approval:

```
Stage 1 (during the visit)
  dictation ─┬─> prescription agent ─┐
             └─> instructions agent ─┴─> DOCTOR: approve / edit / send back

Stage 2 (after the visit)
  dictation ───> visit note agent ───> verifier ───> DOCTOR: approve / edit / send back
                                                         │
                                                         └─> final documentation
```

- **Prescription agent:** extracts drugs, doses and dosing from the dictation. Drug names are matched against the Polish Registry of Medicinal Products (Rejestr Produktów Leczniczych) by plain code (fuzzy matching), not by the LLM, so nothing outside the registry can end up on a prescription. The registry lists common names in Latin (`Amoxicillinum`) while doctors dictate Polish names (`amoksycylina`), so both are reduced to a shared spelling before matching. Missing fields are flagged instead of guessed.
- **Instructions agent:** writes patient instructions in plain language, warning signs and the follow-up date.
- **Visit note agent:** writes the visit record: history, physical examination, diagnosis and a suggested ICD-10 code.
- **Verifier:** flags anything in the note that is not supported by the dictation, and anything important from the dictation that is missing.
- **Doctor:** the graph pauses (`interrupt()`) until the doctor approves the draft, possibly after editing it, or sends it back to the agents with a comment.

Patient data (name, chronic conditions, regular medication, allergies) is loaded from a database by PESEL, so the doctor only dictates what happened during the visit. Medication and instructions approved in stage 1 are included in the visit note automatically.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m src.drugs    # download the drug registry (~17 MB) into data/
cp .env.example .env
```

Fill in `.env` with an API key and the model in `provider:model` format, for example `anthropic:claude-haiku-5-5`, `openai:gpt-5-mini` or `ollama:llama3.1` for a local model.

The registry is the official export from [rejestry.ezdrowie.gov.pl](https://rejestry.ezdrowie.gov.pl/rpl/search/public), regenerated daily. It is not committed; the file keeps its original name with the snapshot date (`Rejestr_Produktow_Leczniczych_calosciowy_stan_na_dzien_YYYYMMDD.csv`) and the newest one in `data/` is used. Only human medicinal products with a valid (not expired or suspended) authorization are considered.

To use audio files instead of text dictation, also install `faster-whisper`.

## Usage

```bash
.venv/bin/python run.py data/visits/visit_001.json
```

For each stage the draft is written to `out/<visit_id>/stageN_draft.json`. Edit the file if needed, then in the terminal:

- press **Enter** to approve the draft (including your edits),
- or type a **comment** to send it back to the agents for revision.

Add `--auto` to approve every draft without waiting, which is useful for running many scenarios in a batch.

### Input

A visit file (`data/visits/*.json`) contains the patient's PESEL and the dictation for both stages, either as text (`stage1_dictation`) or as a path to an audio file (`stage1_audio`).

### Output

| File | Contents |
|---|---|
| `documentation.json` | Final visit note with the approved prescription and instructions |
| `stageN_draft.json` / `stageN_approved.json` | Draft and approved version of each stage |
| `log.json` | Research metrics: review time, decision, draft-to-final similarity, number of verifier issues |

## Project structure

```
run.py            CLI runner; the doctor approves documents by editing JSON files
src/graph.py      LangGraph graph: agents and approval points
src/schemas.py    Pydantic schemas of the documents
src/drugs.py      Drug registry download and matching of dictated drug names
src/llm.py        Model selection (one place to switch provider)
src/stt.py        Speech-to-text with faster-whisper (optional)
data/             Synthetic patients and sample visits (plus the downloaded drug registry)
```

Code, identifiers and document fields are in English. LLM prompts (including the schema field descriptions) and dictations are in Polish, since the assistant produces Polish medical documentation; the generated content is therefore in Polish too.

## Limitations and next steps

- Visit state is kept in memory, so both stages must run in a single session. To be replaced with a SQLite checkpointer.
- Planned: Streamlit interface with recording from the microphone and editable forms.
- Planned: a set of 20–30 synthetic visit scenarios and a comparison of cloud vs. local models and of manual vs. assisted documentation.
