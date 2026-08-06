# Pipeline Contract

## Service standard

Every extraction, rule, constraint, index row, and downstream coaching action must support a **Genuine Outstanding Coaching Intelligence** service.

This means pipeline output must be:
- evidence-grounded
- operational, not vague
- safe enough to constrain coaching behavior
- specific enough to drive real decisions
- usable for low-resource riders when supported by the source
- aligned with the principle that journals are primary evidence and books are secondary support

If an extracted output is not actionable, safe, specific, and decision-ready, it is not good enough for Alpine AI.

## Quick start

```bash
# 1. Set your API key
set ALPIE_API_KEY=<your-key>        # Windows
export ALPIE_API_KEY=<your-key>     # macOS / Linux

# 2. Install dependencies
pip install pymupdf pi169
pip install pytesseract Pillow       # optional — needed only for scanned PDFs
# + install Tesseract OCR: https://github.com/UB-Mannheim/tesseract/wiki

# 3. Drop PDFs into the input folder
#    data/raw/PDF_RAW/

# 4. Run the pipeline
python scripts/import_pdfs.py
```

---

## Folder structure

```
data/
├── raw/
│   └── PDF_RAW/              ← DROP NEW PDFs HERE
├── processed/
│   ├── PDF_PROCESSED/
│   │   ├── Journals/
│   │   │   └── <Domain>/     ← PDFs moved here after extraction
│   │   └── Books/
│   │       └── <Domain>/
│   └── notes/
│       └── <Domain>/
│           └── <Sub_Topic>/  ← Markdown notes per paper
└── indexes/
    ├── Master_Index.csv       ← Full structured index (all papers)
    ├── Manual_Review_Queue.csv← Papers flagged for human review
    └── failed_chunks/         ← LLM extraction failures (JSON)
```

---

## What the pipeline does (per PDF)

1. **Scanned PDF check** — if < 50 chars/page average, runs OCR via Tesseract
2. **Priority detection** — DOI/ISSN/journal keywords → `journal` or `book`
3. **LLM metadata extraction** — text chunked into ~1000-word windows, sent to the Alpine AI LLM
4. **Coaching node extraction** — LLM populates: `Coaching_Principles`, `Constraints`, `Decision_Rules`, `Individualization_Factors`, `Recovery_Heuristics`
5. **Document type classification** — rule-based signals + optional LLM recheck
6. **PDF moved** → `data/processed/PDF_PROCESSED/<journal|book>/<domain>/`
7. **Markdown note saved** → `data/processed/notes/<domain>/<sub_topic>/`
8. **CSV row written** → `data/indexes/Master_Index.csv`
9. **Low-confidence rows flagged** → `data/indexes/Manual_Review_Queue.csv`

---

## Metadata schema

See `COACHING_METADATA_SCHEMA.md` for the full field reference.

Key fields the coaching engine uses:

| Field | Used by |
|-------|---------|
| `Decision_Rules` | Coaching rule engine — compiled into IF-THEN logic |
| `Constraints` | Rule engine safety layer — never overridden |
| `Coaching_Principles` | Plan generator — justification text |
| `Individualization_Factors` | Rider profile matching |
| `Recovery_Heuristics` | Fatigue detector and recovery scheduler |
| `Confidence_Ceiling` | Prevents citing lab-dependent rules to low-resource riders |
| `Female_Physiology_Relevant` | Routes female-specific rules to female riders |
| `Durability_Relevant` | Activates multi-hour / repeated-day rule set |

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ALPIE_API_KEY` | ✅ | Authentication for the Alpine AI LLM |
| `TESSERACT_CMD` | Optional | Full path to tesseract.exe if not on PATH |
| `ENABLE_LLM_TYPE_RECHECK` | Optional | Set `1` to enable LLM re-verification of document type |

---

## pCloud sync

The `data/` folder is **not committed to Git** (see `.gitignore`).
Sync strategy:
- `data/raw/PDF_RAW/` → pCloud `/AlpineAI/raw/` — source of truth for all PDFs
- `data/indexes/` → pCloud `/AlpineAI/indexes/` — Master_Index backup
- `data/processed/` → pCloud `/AlpineAI/processed/` — processed PDFs and notes

PDFs stay outside Git permanently. Only pipeline code is version-controlled.
