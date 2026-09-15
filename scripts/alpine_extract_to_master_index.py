import os
import re
import csv
import shutil
import json
import time
import fitz  # PyMuPDF
from pathlib import Path
from datetime import date
from pi169 import Pi169Client

# ─────────────────────────────────────────

# CONFIGURATION

# ─────────────────────────────────────────


# REPLACE WITH THIS


# ✅ CORRECT - reads the key from the environment variable
CLIENT = Pi169Client(api_key=os.environ.get("ALPIE_API_KEY"))
LLM_MODEL = "alpie-32b"


# Paths relative to the AlpineAi_Library repo root
BASE_DIR       = Path(__file__).resolve().parent.parent
RAW_FOLDER     = BASE_DIR / "data" / "raw" / "PDF_RAW"
PROCESSED_ROOT = BASE_DIR / "data" / "processed" / "PDF_PROCESSED"
NOTES_ROOT     = BASE_DIR / "data" / "processed" / "notes"
INDEX_FILE     = BASE_DIR / "data" / "indexes" / "Master_Index.csv"
MANUAL_REVIEW_FILE = BASE_DIR / "data" / "indexes" / "Manual_Review_Queue.csv"

MAX_OCR_PAGES  = 999   # OCR all pages (capped by MAX_CHARS budget)
MAX_CHARS      = 500000  # ~500k chars allows full books to be chunked properly
TODAY          = date.today().isoformat()

# Extraction reliability settings
CHUNK_MIN_WORDS = 800
CHUNK_MAX_WORDS = 1200
CHUNK_TARGET_WORDS = 1000
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0
FAILED_CHUNKS_DIR = BASE_DIR / "data" / "indexes" / "failed_chunks"

JOURNAL_KEYWORDS = [
    "journal", "sports medicine", "applied physiology",
    "international journal", "european journal", "frontiers",
    "nutrients", "doi", "issn", "vol.", "issue"
]
BOOK_KEYWORDS = [
    "textbook", "handbook", "manual",
    "guide", "coaching", "isbn", "chapter"
]

CSV_FIELDS = [
    # ── Identity ──────────────────────────────────────────────────────────────
    "Paper_ID", "Title", "Authors", "Year", "Domain", "Sub_Topic",
    "Evidence_Type", "Document_Type", "Doc_Type_Confidence", "Manual_Review_Required",
    "Evidence_Score", "Source_Priority", "Source",

    # ── Applicability flags ───────────────────────────────────────────────────
    "Cycling_Specificity", "Elite_Applicability", "Resource_Level",
    "Female_Physiology_Relevant",   # yes | no | partial
    "Altitude_Heat_Relevant",       # yes | no | partial
    "Youth_Applicable",             # yes | no | partial
    "Masters_Applicable",           # yes | no | partial
    "Durability_Relevant",          # yes | no — multi-hour / repeated-day demands

    # ── Core content ─────────────────────────────────────────────────────────
    "Main_Finding", "Practical_Application", "Low_Resource_Applicability",

    # ── Coaching knowledge nodes ──────────────────────────────────────────────
    # Each field holds a pipe-separated list of short declarative statements.
    # These are the primary inputs to the coaching rule engine.
    "Coaching_Principles",          # "Progressive overload before intensification | ..."
    "Constraints",                  # "Do not exceed 2 hard sessions/week | ..."
    "Decision_Rules",               # "IF ATL>CTL*1.3 THEN reduce load by 20% | ..."
    "Individualization_Factors",    # "Adjust for menstrual phase | Altitude responder status | ..."
    "Recovery_Heuristics",          # "48h minimum between VO2max efforts | ..."

    # ── Governance ───────────────────────────────────────────────────────────
    "Superseded_By",                # Paper_ID of newer paper that overrides this one
    "Confidence_Ceiling",           # max Evidence_Score when no power meter/HRM available (1-5)

    # ── Search & linkage ─────────────────────────────────────────────────────
    "Tags", "Related_Papers", "Linked_Features",

    # ── Provenance ───────────────────────────────────────────────────────────
    "PDF_Filename", "Obsidian_Path",
    "Acquisition_Status", "Date_Added"
]

VALID_EVIDENCE_TYPES = {
    "journal_article", "systematic_review", "meta_analysis",
    "consensus_statement", "book", "book_chapter"
}
VALID_HML = {"High", "Medium", "Low"}
VALID_DOCUMENT_TYPES = {
    "journal_article", "book", "book_chapter", "report", "thesis", "other"
}

BEGINNER_FOUNDATION_KEYWORDS = (
    "beginner", "beginners", "novice", "novices", "foundational", "foundation",
    "consistency", "adherence", "compliance", "early stage", "early-stage",
    "returning", "rebuilding", "progression", "progressive overload",
)

# ─────────────────────────────────────────

# EXTRACTION PROMPT

# ─────────────────────────────────────────

EXTRACTION_PROMPT = """
You are a sports-science knowledge engineer for an elite cycling AI coach called Alpine AI.
Your mission: extract metadata that enables GENUINE, OUTSTANDING coaching intelligence.
Every field you fill in becomes a rule, a constraint, or a coaching decision the AI makes.
Every output must be good enough to support a Genuine Outstanding Coaching Intelligence service.
Output JSON only. Do not explain your reasoning. Do not include <think>, prose, markdown, or any surrounding text.

Hierarchy rule: journals are primary evidence; books are secondary.

CRITICAL — do not output placeholder or fabricated content:
- Never output the literal word "string" (or any other JSON-type name) as a field's value. Every example below marked "string" is a TYPE hint, not a value to copy — if you cannot determine the real value from the source text, use an empty string "" (or empty list [] for array fields) instead.
- Never invent author names such as "John Doe", "Jane Smith", "Author Name", "Unknown Author", or generic placeholder surnames. If the true authors cannot be identified from the text, return an empty list [].
- Never pad a list field with filler items to reach some target count (e.g. "Principle 1", "Tag 2", "Encoded finding"). Only include items backed by genuine, specific content you found in the source text — a short, real list is better than a padded one.
- If the source text is too garbled, truncated, or unreadable to extract real content, do not fabricate plausible-sounding findings — set completeness_score and extraction_confidence low and leave the affected fields empty instead.

Return ONLY valid JSON with EXACTLY these fields:

{
  "title":                      "<exact paper title as printed in the source text>",
  "authors":                    ["<full author name as printed>"],
  "year":                       "<4-digit publication year, or empty string if not stated>",
  "document_type":              "journal_article | book | book_chapter | report | thesis | other",
  "evidence_type":              "journal_article | systematic_review | meta_analysis | consensus_statement | book | book_chapter",
  "source":                     "<journal name, publisher, or book title as printed>",
  "domain":                     "<one domain, e.g. Load_Monitoring, Recovery, Nutrition, Training_Prescription, Female_Physiology, Environmental, AI_Data_Science, Core_Physiology, Durability>",
  "sub_topic":                  "<specific sub-topic, e.g. HRV_Monitoring, Fueling_Strategies, Sleep_Optimization>",
  "sport":                      "<primary sport studied, e.g. cycling, running, triathlon>",
  "population":                 "<studied population as described, e.g. 'trained male road cyclists'>",
  "sample_size":                "<number of participants as stated, or empty string if not stated>",
  "training_level":             "<e.g. recreational, trained, elite, or empty string if not stated>",
  "cycling_specificity":        "High | Medium | Low",
  "elite_applicability":        "High | Medium | Low",
  "resource_level":             "Low | Medium | High",

  "female_physiology_relevant": "yes | no | partial",
  "altitude_heat_relevant":     "yes | no | partial",
  "youth_applicable":           "yes | no | partial",
  "masters_applicable":         "yes | no | partial",
  "durability_relevant":        "yes | no",

  "evidence_strength":          "integer 1-5",
  "confidence_ceiling":         "integer 1-5 — max Evidence_Score applicable when rider has no power meter or HR monitor",

  "main_finding":               "string — key result in 2-3 sentences",
  "practical_application":      "string — concrete IF-THEN coaching actions",
  "low_resource_applicability": "string — how Alpine AI can apply this finding using only RPE, wellness scores, distance, and time with no power meter or HR monitor",

  "coaching_principles":        ["string — short declarative principle, e.g. 'Progressive overload before intensification'"],
  "constraints":                ["string — hard constraint the coach must not violate, e.g. 'No more than 2 hard sessions per week'"],
  "decision_rules":             ["string — IF-THEN rules, e.g. 'IF ATL > CTL * 1.3 THEN reduce weekly load by 20%'"],
  "individualization_factors":  ["string — variables that change the rule, e.g. 'Adjust for menstrual cycle phase', 'Altitude responder status'"],
  "recovery_heuristics":        ["string — recovery timing rules, e.g. '48h minimum between VO2max efforts', 'Sleep > 7h before key sessions'"],

  "completeness_score":         "float 0.0-1.0",
  "actionability_score":        "float 0.0-1.0",
  "tags":                       ["<short topical keyword actually relevant to this paper>"],
  "related_papers":             ["AuthorLastName_Year_ShortDescriptor"],
  "linked_features":            ["Recovery Score | Adaptive FTP | Fatigue Warnings | Training Load Alerts | Nutrition Timing Engine | Environmental Adaptation"],
  "extraction_confidence":      "float 0.0-1.0"
}

Scoring rules:
- evidence_strength: 5=meta-analysis/systematic review, 4=RCT, 3=good cohort study, 2=case study/expert opinion, 1=anecdote
- confidence_ceiling: lower by 1 if findings require power measurement; lower by 2 if findings require lab tests
- completeness_score: how fully this paper answers a COACHING DECISION question (1.0 = complete IF-THEN answer)
- actionability_score: how directly findings translate to IF-THEN coaching rules (1.0 = explicit protocol given)
- practical_application: MUST be written as concrete coaching actions, not vague summaries
- low_resource_applicability: always fill — critical for Alpine AI's underserved riders
- coaching_principles: distill 1-4 durable coaching truths this paper supports, written as operational principles
- constraints: list every hard rule this paper implies the coach MUST NOT violate, including safety and load ceilings
- decision_rules: write as strict IF-THEN-ELSE logic the rule engine can execute; avoid commentary, qualifiers, and hedging
- individualization_factors: list every variable that modifies how this rule applies to different riders and contexts
- recovery_heuristics: list all specific recovery timing guidelines from this paper, with clear trigger conditions when possible
- coaching-grade output: prefer precise, executable language over academic prose
- service standard: every extracted field must be safe, specific, evidence-grounded, and decision-ready enough for a Genuine Outstanding Coaching Intelligence service
- journal-priority guardrail: if DOI, ISSN, volume/issue, or article language is present, treat the source as journal-first
- if the source is a journal article, never downgrade it to book-like content because it mentions a textbook concept
- document_type must be one of the strict enum choices exactly
"""

# ─────────────────────────────────────────

# UTILITIES

# ─────────────────────────────────────────

def is_scanned(path: Path, sample_pages: int = 3) -> bool:
    doc = fitz.open(str(path))
    pages = min(sample_pages, len(doc))
    total = sum(len(doc[i].get_text()) for i in range(pages))
    doc.close()
    return (total / max(pages, 1)) < 50


def extract_text_direct(path: Path) -> str:
    doc = fitz.open(str(path))
    text = "".join(page.get_text() for page in doc)
    doc.close()
    return text


def extract_text_ocr(path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image
        import io
    except ImportError:
        print("  pytesseract or Pillow not installed. Falling back to direct extraction.")
        return extract_text_direct(path)

    # Prefer explicit env var, then PATH lookup
    tesseract_cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not tesseract_cmd:
        print("  Tesseract executable not found. Falling back to direct extraction.")
        return extract_text_direct(path)

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    doc = fitz.open(str(path))
    text = ""
    try:
        for i, page in enumerate(doc):
            if i >= MAX_OCR_PAGES:
                break
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            try:
                text += pytesseract.image_to_string(img)
            except pytesseract.pytesseract.TesseractNotFoundError:
                print("  Tesseract not callable. Falling back to direct extraction.")
                return extract_text_direct(path)
            except Exception as e:
                print(f"  OCR warning on page {i+1}: {e}")
    finally:
        doc.close()

    if not text.strip():
        print("  OCR returned no text. Falling back to direct extraction.")
        return extract_text_direct(path)

    return text


def detect_priority(text: str, filename: str) -> str:
    combined = (text[:3000] + filename).lower()
    j_score  = sum(1 for k in JOURNAL_KEYWORDS if k in combined)
    b_score  = sum(1 for k in BOOK_KEYWORDS    if k in combined)

    doi_match  = re.search(r'\b10\.\d{4,}/\S+', combined)
    issn_match = re.search(r'issn\s*[\d\-x]+', combined)
    journal_score = j_score
    book_score = b_score

    if doi_match:
        journal_score += 3
    if issn_match:
        journal_score += 3
    if re.search(r'\bjournal\b', combined):
        journal_score += 2
    if re.search(r'\bsystematic review\b', combined):
        journal_score += 2
    if re.search(r'\bmeta-analysis\b', combined):
        journal_score += 2
    if re.search(r'\bvol\.?\b', combined) or re.search(r'\bvolume\b', combined):
        journal_score += 1
    if re.search(r'\bissue\b', combined) or re.search(r'\bpages?\b', combined):
        journal_score += 1
    if re.search(r'\bmixed method study\b', combined) or re.search(r'\bstudy\b', combined):
        journal_score += 1
    if re.search(r'\bparticipants?\b', combined) or re.search(r'\bmethods?\b', combined):
        journal_score += 1
    if re.search(r'\babstract\b', combined) or re.search(r'\bresults?\b', combined):
        journal_score += 1

    if re.search(r'\bisbn(?:-1[03])?\b', combined):
        book_score += 3
    if re.search(r'\bpublisher\b|\bpress\b', combined):
        book_score += 2
    if re.search(r'\bedition\b|\bchapter\b', combined):
        book_score += 1
    if re.search(r'\bhandbook\b|\btextbook\b', combined):
        book_score += 2

    if journal_score >= book_score + 2:
        return "journal"
    if book_score >= journal_score + 2:
        return "book"
    return "journal" if journal_score >= book_score else "book"


def clean_folder_name(name: str) -> str:
    return re.sub(r'[^\w]', '_', name).strip('_')


_PAPER_ID_FRONTMATTER_RE = re.compile(r"^paper_id:\s*ALP-(\d+)-(\d+)\s*$", re.MULTILINE)


def _max_paper_id_num_in_notes(prefix: str) -> int:
    """Highest Paper_ID number ever written to a note file, even if
    Master_Index.csv has since been trimmed, reset, or rebuilt. Notes are
    never deleted by cleanup scripts, so this is the durable high-water mark."""
    max_num = 0
    if not NOTES_ROOT.exists():
        return max_num
    for note_path in NOTES_ROOT.rglob("*.md"):
        try:
            head = note_path.read_text(encoding="utf-8", errors="ignore")[:500]
        except OSError:
            continue
        m = _PAPER_ID_FRONTMATTER_RE.search(head)
        if m and f"ALP-{m.group(1)}-" == prefix:
            max_num = max(max_num, int(m.group(2)))
    return max_num


def _max_paper_id_num_in_manual_review(prefix: str) -> int:
    max_num = 0
    if not MANUAL_REVIEW_FILE.exists():
        return max_num
    with open(MANUAL_REVIEW_FILE, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pid = row.get("Paper_ID", "")
            if pid.startswith(prefix) and pid.split("-")[-1].isdigit():
                max_num = max(max_num, int(pid.split("-")[-1]))
    return max_num


def paper_id_floor() -> int:
    """Durable high-water mark for Paper_ID numbering, independent of the
    current (possibly-trimmed) Master_Index.csv. Call once per run."""
    prefix = f"ALP-{date.today().year}-"
    return max(
        _max_paper_id_num_in_notes(prefix),
        _max_paper_id_num_in_manual_review(prefix),
    )


def generate_paper_id(existing_rows: list, floor: int = 0) -> str:
    year = date.today().year
    prefix = f"ALP-{year}-"
    used_nums = [
        int(r["Paper_ID"].split("-")[-1])
        for r in existing_rows
        if r.get("Paper_ID", "").startswith(prefix) and r["Paper_ID"].split("-")[-1].isdigit()
    ]
    next_num = max(used_nums + [floor], default=0) + 1
    return f"{prefix}{next_num:04d}"


def load_index() -> list:
    if not INDEX_FILE.exists():
        return []
    with open(INDEX_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_index(rows: list):
    with open(INDEX_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def save_manual_review_queue(review_rows: list[dict]):
    if not review_rows:
        return

    existing = []
    if MANUAL_REVIEW_FILE.exists():
        with open(MANUAL_REVIEW_FILE, newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))

    # Dedupe by Paper_ID if present, else by filename+title.
    seen = set()
    merged = []
    for row in existing + review_rows:
        key = row.get("Paper_ID") or f"{row.get('PDF_Filename','')}|{row.get('Title','')}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)

    review_fields = [
        "Paper_ID", "Title", "Year", "Document_Type", "Doc_Type_Confidence",
        "Manual_Review_Reason", "Source", "Evidence_Type", "PDF_Filename", "Date_Added"
    ]
    with open(MANUAL_REVIEW_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=review_fields)
        writer.writeheader()
        writer.writerows(merged)


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "unknown"


def _log_failure_reason(reason: str, context: str):
    print(f"  [{reason}] {context}")


def _classify_extraction_error(exc: Exception) -> str:
    msg = str(exc).lower()
    exc_name = exc.__class__.__name__.lower()

    if "timeout" in msg or "timed out" in msg or "timeouterror" in exc_name:
        return "timeout"
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return "http_429"

    status_5xx = re.search(r"\b5\d\d\b", msg)
    if status_5xx:
        return "http_5xx"

    if "connection" in msg or "temporar" in msg or "network" in msg:
        return "connection_error"

    return "llm_error"


def _is_retryable_reason(reason: str) -> bool:
    return reason in {"timeout", "http_429", "http_5xx", "connection_error"}


def _save_failed_chunk(
    pdf_name: str,
    chunk_index: int,
    reason: str,
    raw_response: str,
    priority: str,
    chunk_preview: str,
    error_message: str = ""
):
    FAILED_CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "date": TODAY,
        "pdf_name": pdf_name,
        "chunk_index": chunk_index,
        "reason": reason,
        "priority": priority,
        "error_message": error_message,
        "chunk_preview": chunk_preview[:1200],
        "raw_response": raw_response,
    }

    filename = (
        f"{_safe_slug(Path(pdf_name).stem)}"
        f"__chunk_{chunk_index:03d}"
        f"__{reason}"
        f"__{int(time.time() * 1000)}.json"
    )
    out_path = FAILED_CHUNKS_DIR / filename
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _split_text_into_chunks(
    text: str,
    min_words: int = CHUNK_MIN_WORDS,
    max_words: int = CHUNK_MAX_WORDS,
    target_words: int = CHUNK_TARGET_WORDS
) -> list[str]:
    words = text.split()
    if not words:
        return []

    chunks = []
    i = 0
    n = len(words)
    while i < n:
        remaining = n - i
        if remaining <= max_words:
            chunk_size = remaining
        else:
            chunk_size = target_words

        if chunk_size < min_words and chunks:
            chunks[-1] = f"{chunks[-1]} {' '.join(words[i:])}".strip()
            break

        chunk_size = max(min_words, min(max_words, chunk_size))
        chunk_words = words[i:i + chunk_size]
        chunks.append(" ".join(chunk_words))
        i += chunk_size

    return chunks


def _close_truncated_json(raw: str) -> str:
    """
    Close truncated JSON by balancing braces.
    Handles cases where LLM response cuts off mid-response.
    """
    if not raw:
        return ""
    
    text = raw.strip()
    
    # Count braces to detect truncation
    open_braces = text.count("{")
    close_braces = text.count("}")
    open_brackets = text.count("[")
    close_brackets = text.count("]")
    
    # Check if balanced
    if open_braces <= close_braces and open_brackets <= close_brackets:
        # Either balanced or more closes than opens; don't try to fix
        return text
    
    # Truncated JSON - need to close it
    # First, close any unterminated strings
    # Count quotes to see if we're in an unterminated string
    quote_count = text.count('"') - (text.count('\\"') if '\\' in text else 0)
    
    if quote_count % 2 != 0:
        # Unterminated string; close it with a quote
        text = text + '"'
    
    # Now close the missing braces and brackets
    # Balance brackets first, then braces
    missing_brackets = open_brackets - close_brackets
    missing_braces = open_braces - close_braces
    
    # Add closing brackets
    text = text + "]" * missing_brackets
    # Add closing braces
    text = text + "}" * missing_braces
    
    return text


def _repair_json_text(raw: str) -> str:
    if not raw:
        return ""

    text = raw.strip()
    
    # First, try to close truncated JSON
    text = _close_truncated_json(text)
    
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    # Keep only the first object-like span when wrappers are present.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    # Fix smart quotes (curly quotes) - do this multiple times to catch all variants
    smartquote_replacements = {
        """: '"',
        """: '"',
        "'": "'",
        "'": "'",
        "′": "'",
        "″": '"',
    }
    for old, new in smartquote_replacements.items():
        text = text.replace(old, new)

    # Fix fields with missing values (e.g., "key":} or "key": ])
    # Replace with "key":null or "key":[]
    text = re.sub(r':\s*([}\]])', r':[]\1', text)  # Array-like fields get []
    
    # Convert Python-ish literals to JSON literals.
    text = re.sub(r"\bNone\b", "null", text)
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)

    # Quote unquoted keys like: {title: "x"} or , authors: [...]
    text = re.sub(r'([,{]\s*)([A-Za-z_][A-Za-z0-9_\- ]*)(\s*:)', r'\1"\2"\3', text)

    # Fix malformed closing patterns like: value,]}  ->  value]}
    # This happens when an array/bracket is improperly nested
    text = re.sub(r',\s*(\])', r'\1', text)  # Remove comma before ]
    
    # Remove trailing commas before object/array close.
    text = re.sub(r",\s*([}\]])", r"\1", text)
    
    # Fix orphaned array close before object close at the end
    # Pattern: ...value]} where the ] doesn't make sense structurally
    # This is a heuristic - if we have ]} at the very end, remove the ]
    if text.rstrip().endswith(']}'):
        # Check if this looks like a malformed close
        # Count unmatched brackets
        if text.count('[') < text.count(']'):
            # More closes than opens; remove the trailing ]
            text = re.sub(r'\]\s*}\s*$', '}', text)

    return text


def _build_fallback_metadata(pdf_path: Path, priority: str) -> dict:
    guessed_year = ""
    year_match = re.search(r"\b(19|20)\d{2}\b", pdf_path.stem)
    if year_match:
        guessed_year = year_match.group(0)

    fallback_type = "journal_article" if priority == "journal" else "book"
    fallback_strength = 3 if priority == "journal" else 2

    return {
        "title": pdf_path.stem,
        "authors": ["Unknown"],
        "year": guessed_year,
        "evidence_type": fallback_type,
        "source": "Unknown",
        "domain": "General",
        "sub_topic": "General",
        "sport": "Cycling",
        "population": "Unknown",
        "sample_size": "Unknown",
        "training_level": "Unknown",
        "cycling_specificity": "Low",
        "elite_applicability": "Low",
        "resource_level": "Low",
        "female_physiology_relevant": "no",
        "altitude_heat_relevant": "no",
        "youth_applicable": "no",
        "masters_applicable": "no",
        "durability_relevant": "no",
        "evidence_strength": fallback_strength,
        "confidence_ceiling": fallback_strength,
        "main_finding": "Automatic extraction failed; metadata requires manual review.",
        "practical_application": "Review source manually before applying coaching decisions.",
        "low_resource_applicability": "Use conservative load progression based on RPE and wellness until reviewed.",
        "coaching_principles": [],
        "constraints": [],
        "decision_rules": [],
        "individualization_factors": [],
        "recovery_heuristics": [],
        "superseded_by": "",
        "completeness_score": 0.1,
        "actionability_score": 0.1,
        "tags": ["needs_manual_review"],
        "related_papers": [],
        "linked_features": [],
        "extraction_confidence": 0.1,
    }


def _merge_metadata(base: dict, incoming: dict) -> dict:
    if not incoming:
        return base

    merged = dict(base)

    list_fields = {
        "authors", "tags", "related_papers", "linked_features",
        "coaching_principles", "constraints", "decision_rules",
        "individualization_factors", "recovery_heuristics",
    }
    numeric_max_fields = {"evidence_strength", "completeness_score", "actionability_score", "extraction_confidence"}
    text_longest_fields = {"main_finding", "practical_application", "low_resource_applicability"}

    for key, val in incoming.items():
        if val in (None, "", [], {}):
            continue

        if key in list_fields and isinstance(val, list):
            existing = merged.get(key, []) if isinstance(merged.get(key), list) else []
            seen = set()
            merged_list = []
            for item in existing + val:
                token = str(item).strip()
                if token and token not in seen:
                    seen.add(token)
                    merged_list.append(token)
            merged[key] = merged_list
            continue

        if key in numeric_max_fields:
            try:
                incoming_num = float(val)
            except (TypeError, ValueError):
                continue
            try:
                existing_num = float(merged.get(key, 0))
            except (TypeError, ValueError):
                existing_num = 0.0
            merged[key] = incoming_num if incoming_num >= existing_num else existing_num
            continue

        if key in text_longest_fields:
            current = str(merged.get(key, ""))
            candidate = str(val)
            if len(candidate.strip()) > len(current.strip()):
                merged[key] = candidate
            continue

        # First useful value wins for identity-like fields.
        if merged.get(key) in (None, "", [], {}):
            merged[key] = val

    return merged


def _build_doc_signal_blob(text: str, data: dict, filename: str = "") -> str:
    parts = [
        filename,
        str(data.get("title", "")),
        str(data.get("source", "")),
        str(data.get("evidence_type", "")),
        str(data.get("document_type", "")),
        str(data.get("main_finding", "")),
        str(data.get("practical_application", "")),
        str(data.get("low_resource_applicability", "")),
        " ".join(data.get("tags", []) if isinstance(data.get("tags", []), list) else []),
        text[:20000],
    ]
    return "\n".join(parts).lower()


def _contains_beginner_foundation_signals(*values: str) -> bool:
    blob = " ".join(str(value or "").lower() for value in values)
    return any(keyword in blob for keyword in BEGINNER_FOUNDATION_KEYWORDS)


def _enrich_beginner_foundation_metadata(data: dict) -> dict:
    enriched = dict(data or {})
    if not _contains_beginner_foundation_signals(
        enriched.get("title", ""),
        enriched.get("main_finding", ""),
        enriched.get("practical_application", ""),
        enriched.get("low_resource_applicability", ""),
        " ".join(enriched.get("coaching_principles", []) if isinstance(enriched.get("coaching_principles", []), list) else []),
        " ".join(enriched.get("decision_rules", []) if isinstance(enriched.get("decision_rules", []), list) else []),
        " ".join(enriched.get("tags", []) if isinstance(enriched.get("tags", []), list) else []),
        enriched.get("training_level", ""),
        enriched.get("population", ""),
    ):
        return enriched

    tags = list(enriched.get("tags", []) or [])
    for tag in ("beginner_foundation", "consistency", "progression"):
        if tag not in tags:
            tags.append(tag)
    enriched["tags"] = tags

    domain = str(enriched.get("domain", "")).strip()
    if not domain or domain.lower() == "general":
        enriched["domain"] = "Training_Prescription"

    sub_topic = str(enriched.get("sub_topic", "")).strip()
    if not sub_topic or sub_topic.lower() in {"general", "unknown"}:
        enriched["sub_topic"] = "Beginner_Progression"

    principles = list(enriched.get("coaching_principles", []) or [])
    decisions = list(enriched.get("decision_rules", []) or [])
    constraints = list(enriched.get("constraints", []) or [])
    heuristics = list(enriched.get("recovery_heuristics", []) or [])

    additions = {
        "coaching_principles": [
            "Build consistency before adding intensity.",
            "Use step-wise progression only after stable completion and manageable fatigue.",
        ],
        "decision_rules": [
            "IF adherence is inconsistent THEN hold load steady and prioritize session completion before progressing intensity.",
            "IF 2-3 weeks of stable completion and tolerable fatigue are achieved THEN add only one progression step at a time.",
        ],
        "constraints": [
            "Do not add multiple progression changes in the same week for beginner or rebuilding riders.",
        ],
        "recovery_heuristics": [
            "Use mostly easy riding and leave at least one low-stress day between more demanding sessions while rebuilding consistency.",
        ],
    }

    for field_name, target_list in (
        ("coaching_principles", principles),
        ("decision_rules", decisions),
        ("constraints", constraints),
        ("recovery_heuristics", heuristics),
    ):
        for item in additions[field_name]:
            if item not in target_list:
                target_list.append(item)

    enriched["coaching_principles"] = principles
    enriched["decision_rules"] = decisions
    enriched["constraints"] = constraints
    enriched["recovery_heuristics"] = heuristics

    low_resource = str(enriched.get("low_resource_applicability", "")).strip()
    if "consistency" not in low_resource.lower():
        extra = "Prioritize consistency, easy riding, and RPE-based step-wise progression before adding harder work."
        enriched["low_resource_applicability"] = f"{low_resource} {extra}".strip() if low_resource else extra

    practical = str(enriched.get("practical_application", "")).strip()
    if "completion" not in practical.lower():
        extra = "Start with simple weekly structure, prioritize completion, and progress only after stable adherence and recovery."
        enriched["practical_application"] = f"{practical} {extra}".strip() if practical else extra

    return enriched


def _has_journal_signals(blob: str) -> bool:
    doi = re.search(r"\b10\.\d{4,9}/\S+", blob)
    journal_name = re.search(r"\bjournal\b|\binternational journal\b|\beuropean journal\b|\bfrontiers\b", blob)
    vol_issue_pages = re.search(
        r"\bvol(?:ume)?\.?\s*\d+|\bissue\s*\d+|\bno\.?\s*\d+|\bpp?\.?\s*\d+\s*[-–]\s*\d+|\b\d+\(\d+\)\s*:\s*\d+",
        blob,
    )
    pub_type_article = re.search(r"publication\s*type[^\n]*article|\barticle\b", blob)
    return bool(doi or journal_name or vol_issue_pages or pub_type_article)


def _has_book_signals(blob: str) -> tuple[bool, bool]:
    isbn = re.search(r"\bisbn(?:-1[03])?\b[:\s]*[0-9xX\-]{10,17}", blob)
    publisher = re.search(r"\bpublisher\b|\bpress\b", blob)
    edition = re.search(r"\b\d+(st|nd|rd|th)\s+edition\b|\bedition\b", blob)
    chapter = re.search(r"\bchapter\b|\bchapter\s+\d+\b", blob)
    book = bool(isbn or publisher or edition)
    chapter_like = bool(chapter)
    return book, chapter_like


def _llm_doc_type_recheck(data: dict) -> tuple[str, str]:
    use_recheck = os.environ.get("ENABLE_LLM_TYPE_RECHECK", "0") == "1"
    if not use_recheck:
        return "", ""

    payload = {
        "title": data.get("title", ""),
        "source": data.get("source", ""),
        "evidence_type": data.get("evidence_type", ""),
        "year": data.get("year", ""),
        "tags": data.get("tags", []),
        "main_finding": data.get("main_finding", ""),
    }

    messages = [
        {
            "role": "system",
            "content": (
                "Classify document type. Return JSON only: "
                '{"document_type":"journal_article|book|book_chapter|report|thesis|other",'
                '"doc_type_confidence":"high|medium|low"}'
            )
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
    ]

    raw, reason = _request_metadata_with_retries(messages, context="doc_type_recheck")
    if reason != "ok":
        return "", ""

    parsed, parse_reason = _parse_metadata_response(raw)
    if parse_reason == "json_parse_error":
        return "", ""

    doc_type = str(parsed.get("document_type", "")).strip().lower()
    conf = str(parsed.get("doc_type_confidence", "")).strip().lower()
    if doc_type not in VALID_DOCUMENT_TYPES:
        doc_type = ""
    if conf not in {"high", "medium", "low"}:
        conf = ""
    return doc_type, conf


def classify_document_type(text: str, data: dict, filename: str = "") -> tuple[str, str, str]:
    llm_type = str(data.get("document_type", "")).strip().lower()
    if llm_type not in VALID_DOCUMENT_TYPES:
        llm_type = "other"

    blob = _build_doc_signal_blob(text=text, data=data, filename=filename)
    has_journal = _has_journal_signals(blob)
    has_book, has_chapter = _has_book_signals(blob)

    # Rule-based override first.
    if has_journal:
        rule_type = "journal_article"
        rule_conf = "high"
        reason = "rule_forced_journal_signals"
    elif has_book and has_chapter:
        rule_type = "book_chapter"
        rule_conf = "high"
        reason = "rule_forced_book_chapter_signals"
    elif has_book:
        rule_type = "book"
        rule_conf = "high"
        reason = "rule_forced_book_signals"
    else:
        rule_type = llm_type
        rule_conf = "medium" if llm_type != "other" else "low"
        reason = "llm_only"

    # Optional LLM re-check only when confidence is not high.
    if rule_conf != "high":
        recheck_type, recheck_conf = _llm_doc_type_recheck(data)
        if recheck_type and recheck_type in VALID_DOCUMENT_TYPES:
            if recheck_type == rule_type and recheck_conf in {"high", "medium"}:
                rule_conf = "high" if recheck_conf == "high" else "medium"
                reason = "llm_recheck_agree"
            elif rule_type == "other" and recheck_conf in {"high", "medium"}:
                rule_type = recheck_type
                rule_conf = "medium"
                reason = "llm_recheck_override"

    return rule_type, rule_conf, reason


# ─────────────────────────────────────────

# EXTRACTION QUALITY VALIDATION

# ─────────────────────────────────────────

_PLACEHOLDER_LIST_ITEM_RE = re.compile(
    r"^\s*(Tag|Principle|Constraint|Rule|Factor|Heuristic|Feature|RelatedPaper)\s*\d+\s*$",
    re.IGNORECASE,
)
_ENCODED_PREFIX_RE = re.compile(r"^\s*Encoded\b", re.IGNORECASE)
_FAKE_AUTHOR_MARKERS = {
    "john doe", "jane smith", "author name", "unknown author",
    "authorlastname", "not specified in the text", "anonymous author",
}
_LIST_FIELDS_TO_CHECK = (
    "coaching_principles", "constraints", "decision_rules",
    "individualization_factors", "recovery_heuristics", "tags",
    "related_papers", "linked_features",
)
_SCALAR_FIELDS_TO_CHECK = ("title", "year", "source", "sport", "population", "sample_size", "training_level")


def detect_extraction_quality_issues(data: dict, pdf_path: Path) -> list[str]:
    """Catch known LLM extraction failure signatures before they get written
    to the index: literal schema-placeholder leaks, padded/numbered filler
    list items, and fabricated placeholder author names.

    A title-vs-filename word-overlap check was tried and dropped: measured
    against the current library, the genuine fabrication case it was meant
    to catch (jaccard ~0.09) sat in the middle of a long tail of completely
    legitimate papers whose PDF filename just doesn't match their real title
    (book chapters, generic download names, retitled papers) - no threshold
    separated the two, so it added noise without adding signal.
    """
    issues: list[str] = []

    if not str(data.get("title", "")).strip() and str(data.get("main_finding", "")).strip():
        issues.append("empty_title_despite_real_content")

    for field in _SCALAR_FIELDS_TO_CHECK:
        val = str(data.get(field, "")).strip()
        if val.lower() == "string":
            issues.append(f"literal_placeholder_string_in_{field}")

    authors = data.get("authors", []) or []
    for a in authors:
        if str(a).strip().lower() in _FAKE_AUTHOR_MARKERS:
            issues.append("fabricated_placeholder_author_name")
            break

    for field in _LIST_FIELDS_TO_CHECK:
        items = data.get(field, []) or []
        for item in items:
            item_str = str(item).strip()
            if _PLACEHOLDER_LIST_ITEM_RE.match(item_str) or _ENCODED_PREFIX_RE.match(item_str):
                issues.append(f"numbered_placeholder_item_in_{field}")
                break

    return issues


def reclassify_existing_rows(existing_rows: list) -> tuple[list, list, int]:
    if not existing_rows:
        return existing_rows, [], 0

    changed = 0
    review_rows = []

    for row in existing_rows:
        synthetic_data = {
            "title": row.get("Title", ""),
            "source": row.get("Source", ""),
            "evidence_type": row.get("Evidence_Type", ""),
            "document_type": row.get("Document_Type", ""),
            "main_finding": row.get("Main_Finding", ""),
            "practical_application": row.get("Practical_Application", ""),
            "low_resource_applicability": row.get("Low_Resource_Applicability", ""),
            "tags": [t.strip() for t in row.get("Tags", "").split(",") if t.strip()],
            "year": row.get("Year", ""),
        }
        blob_text = "\n".join(
            [
                row.get("Title", ""),
                row.get("Evidence_Type", ""),
                row.get("Main_Finding", ""),
                row.get("Practical_Application", ""),
                row.get("Low_Resource_Applicability", ""),
                row.get("Tags", ""),
                row.get("PDF_Filename", ""),
            ]
        )
        new_type, new_conf, reason = classify_document_type(blob_text, synthetic_data, row.get("PDF_Filename", ""))

        prev_type = row.get("Document_Type", "")
        prev_conf = row.get("Doc_Type_Confidence", "")

        row["Document_Type"] = new_type
        row["Doc_Type_Confidence"] = new_conf
        row["Manual_Review_Required"] = "yes" if new_conf == "low" else "no"

        if prev_type != new_type or prev_conf != new_conf:
            changed += 1

        if new_conf == "low":
            review_rows.append(
                {
                    "Paper_ID": row.get("Paper_ID", ""),
                    "Title": row.get("Title", ""),
                    "Year": row.get("Year", ""),
                    "Document_Type": new_type,
                    "Doc_Type_Confidence": new_conf,
                    "Manual_Review_Reason": reason,
                    "Source": row.get("Source", ""),
                    "Evidence_Type": row.get("Evidence_Type", ""),
                    "PDF_Filename": row.get("PDF_Filename", ""),
                    "Date_Added": row.get("Date_Added", TODAY),
                }
            )

    return existing_rows, review_rows, changed


# ─────────────────────────────────────────

# LLM EXTRACTION

# ─────────────────────────────────────────

def _extract_json_object(raw: str) -> dict:
    if not raw:
        return {}

    text = raw.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    # Try direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Fallback: parse first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return {}

    return {}


def _strip_leading_prose(raw: str) -> str:
    """Strip reasoning/explanation before the first JSON object."""
    if not raw:
        return ""
    
    text = raw.strip()
    # Find the first opening brace
    brace_idx = text.find("{")
    
    if brace_idx > 0:
        # Check if there's prose before the brace
        before_brace = text[:brace_idx].strip()
        if before_brace:
            # There's prose before the JSON; strip it
            return text[brace_idx:]
    
    return text


def _parse_metadata_response(raw: str) -> tuple[dict, str]:
    # Level 0: Try direct extraction (might work if already clean)
    parsed = _extract_json_object(raw)
    if parsed:
        return parsed, "ok"

    # Level 1: Strip leading prose FIRST before any repair
    prose_stripped = _strip_leading_prose(raw)
    if prose_stripped != raw:
        # Successfully stripped prose; try parsing the cleaned version
        try:
            parsed_prose = json.loads(prose_stripped)
            if isinstance(parsed_prose, dict):
                return parsed_prose, "json_repaired_prose_stripped"
        except Exception:
            pass
        
        # Try repair on the prose-stripped version
        repaired_from_stripped = _repair_json_text(prose_stripped)
        if repaired_from_stripped:
            try:
                repaired_obj = json.loads(repaired_from_stripped)
                if isinstance(repaired_obj, dict):
                    return repaired_obj, "json_repaired_from_prose_strip"
            except Exception:
                pass

    # Level 2: Try repair on original raw (if no prose was stripped)
    repaired_text = _repair_json_text(raw)
    if repaired_text:
        try:
            repaired_obj = json.loads(repaired_text)
            if isinstance(repaired_obj, dict):
                return repaired_obj, "json_repaired"
        except Exception:
            pass

    # Level 3: Try fenced code blocks
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        try:
            fenced_obj = json.loads(fenced.group(1))
            if isinstance(fenced_obj, dict):
                return fenced_obj, "json_repaired"
        except Exception:
            pass

    return {}, "json_parse_error"


def _build_deterministic_fallback(chunk_text: str, priority: str, raw_response: str = "") -> dict:
    """
    Level 3: Deterministic fallback extractor.
    Fills critical fields from rule-based extraction when LLM fails.
    """
    # Extract first ~100 words as description if no LLM output
    words = chunk_text.split()[:100]
    description = " ".join(words) if words else "Unable to extract description"
    
    # Try to detect domain from keywords
    domain = "General"
    sub_topic = "Unknown"
    
    text_lower = (chunk_text + raw_response).lower()
    
    domain_keywords = {
        "Training_Prescription": ["interval", "workout", "training", "session", "ride", "effort", "intensity"],
        "Recovery": ["recovery", "rest", "sleep", "fatigue", "overreaching", "overtraining"],
        "Female_Physiology": ["female", "women", "menstrual", "menopause", "pregnancy", "hormone"],
        "Durability": ["chronic", "long-term", "sustainability", "injury prevention", "joint", "career"],
        "Nutrition": ["nutrition", "diet", "carbohydrate", "protein", "fuel", "hydration"],
        "Load_Monitoring": ["tss", "training stress", "load", "rpm", "watt", "power"],
    }
    
    for dom, keywords in domain_keywords.items():
        if any(kw in text_lower for kw in keywords):
            domain = dom
            break
    
    return {
        "Domain": domain,
        "Sub_Topic": sub_topic,
        "Decision_Rules": description,
        "Constraints": "Extracted via deterministic fallback (LLM failed)",
        "Coaching_Principles": "",
        "Individualization_Factors": "",
        "Recovery_Heuristics": "",
        "Evidence_Type": "Unknown",
        "Key_Insights": "",
        "Practical_Application": "",
        "Athlete_Profile_Relevance": "Unknown",
    }


def _retry_with_stricter_prompt(
    chunk_text: str,
    priority: str,
    pdf_path: Path,
    chunk_index: int,
    total_chunks: int,
) -> tuple[dict, str]:
    """
    Level 2: Retry extraction with a shorter chunk and stricter "JSON only" prompt.
    Returns (parsed_dict, status_reason).
    """
    # Use shorter chunk (50% of original)
    words = chunk_text.split()
    shorter_chunk = " ".join(words[:len(words) // 2])
    
    if not shorter_chunk.strip():
        return {}, "level2_chunk_too_short"
    
    strict_prompt = (
        "You are a JSON extractor. Return ONLY valid JSON object. "
        "No text before or after. No explanations. No markdown. Just raw JSON.\n\n"
        "JSON structure:\n"
        "{\n"
        '  "Domain": "string",\n'
        '  "Sub_Topic": "string",\n'
        '  "Decision_Rules": "string",\n'
        '  "Constraints": "string",\n'
        '  "Coaching_Principles": "string",\n'
        '  "Individualization_Factors": "string",\n'
        '  "Recovery_Heuristics": "string",\n'
        '  "Evidence_Type": "string",\n'
        '  "Key_Insights": "string",\n'
        '  "Practical_Application": "string",\n'
        '  "Athlete_Profile_Relevance": "string"\n'
        "}"
    )
    
    messages = [
        {"role": "system", "content": strict_prompt},
        {
            "role": "user",
            "content": (
                f"Source priority: {priority}\n"
                f"Chunk {chunk_index}/{total_chunks} (retry with shorter text)\n\n"
                f"Text:\n{shorter_chunk[:MAX_CHARS]}"
            )
        }
    ]
    
    try:
        # Try with response_format first (if supported); fall back to without if not
        try:
            response = CLIENT.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0,  # Even stricter than normal
                stream=False,
            )
        except TypeError as e:
            # response_format not supported; retry without it
            if "response_format" in str(e):
                response = CLIENT.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    temperature=0.0,
                    stream=False,
                )
            else:
                raise
        
        raw_response = response.choices[0].message.content or ""
        
        # Try all parsing attempts on the retry response
        parsed = _extract_json_object(raw_response)
        if parsed:
            return parsed, "level2_success_direct"
        
        prose_stripped = _strip_leading_prose(raw_response)
        if prose_stripped != raw_response:
            try:
                parsed = json.loads(prose_stripped)
                if parsed:
                    return parsed, "level2_success_prose_stripped"
            except Exception:
                pass
        
        repaired = _repair_json_text(raw_response)
        if repaired:
            try:
                parsed = json.loads(repaired)
                if parsed:
                    return parsed, "level2_success_repaired"
            except Exception:
                pass
        
        return {}, "level2_failed_no_valid_json"
    
    except Exception as e:
        return {}, f"level2_failed_llm_error: {str(e)[:50]}"


def _request_metadata_once(messages: list[dict]) -> str:
    # Explicitly request non-streaming mode to parse one complete response.
    try:
        response = CLIENT.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
            stream=False,
        )
    except TypeError:
        # Backward compatibility for SDKs without response_format support.
        response = CLIENT.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.1,
            stream=False,
        )

    return response.choices[0].message.content or ""


def _request_metadata_with_retries(messages: list[dict], context: str) -> tuple[str, str]:
    last_reason = "llm_error"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return _request_metadata_once(messages), "ok"
        except Exception as exc:
            reason = _classify_extraction_error(exc)
            last_reason = reason
            _log_failure_reason(reason, f"{context} (attempt {attempt}/{MAX_RETRIES}): {exc}")

            if attempt < MAX_RETRIES and _is_retryable_reason(reason):
                sleep_s = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                time.sleep(sleep_s)
                continue
            break

    return "", last_reason


def extract_metadata(text: str, priority: str, pdf_path: Path) -> dict:
    chunks = _split_text_into_chunks(text)
    if not chunks:
        return _build_fallback_metadata(pdf_path, priority)

    print(f"  Metadata chunks: {len(chunks)} ({CHUNK_MIN_WORDS}-{CHUNK_MAX_WORDS} words target)")

    aggregated = {}
    successful_chunks = 0

    for idx, chunk in enumerate(chunks, start=1):
        chunk_preview = chunk[:300].replace("\n", " ")

        messages = [
            {"role": "system", "content": EXTRACTION_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Source priority: {priority}\n"
                    f"Chunk {idx}/{len(chunks)}\n\n"
                    "Return ONLY a JSON object (no markdown).\n\n"
                    f"Text:\n{chunk[:MAX_CHARS]}"
                )
            }
        ]

        raw_response, req_reason = _request_metadata_with_retries(
            messages,
            context=f"{pdf_path.name} chunk {idx}/{len(chunks)}"
        )

        if req_reason != "ok":
            _save_failed_chunk(
                pdf_name=pdf_path.name,
                chunk_index=idx,
                reason=req_reason,
                raw_response=raw_response,
                priority=priority,
                chunk_preview=chunk_preview,
                error_message="request_failed_after_retries",
            )
            continue

        parsed, parse_reason = _parse_metadata_response(raw_response)
        
        # ─ RECOVERY STACK: Three levels of fallback ─
        if parse_reason == "json_parse_error":
            _log_failure_reason(
                "json_parse_error",
                f"{pdf_path.name} chunk {idx}/{len(chunks)} - attempting Level 2 retry"
            )
            
            # Level 2: Retry with stricter prompt and shorter chunk
            parsed, level2_reason = _retry_with_stricter_prompt(
                chunk,
                priority,
                pdf_path,
                idx,
                len(chunks)
            )
            
            if parsed and "level2_success" in level2_reason:
                _log_failure_reason(level2_reason, f"{pdf_path.name} chunk {idx}/{len(chunks)}")
                aggregated = _merge_metadata(aggregated, parsed)
                successful_chunks += 1
                continue
            else:
                _log_failure_reason(
                    level2_reason,
                    f"{pdf_path.name} chunk {idx}/{len(chunks)} - attempting Level 3 fallback"
                )
                
                # Level 3: Deterministic fallback
                parsed = _build_deterministic_fallback(chunk, priority, raw_response)
                _log_failure_reason(
                    "level3_deterministic_fallback",
                    f"{pdf_path.name} chunk {idx}/{len(chunks)}: fallback applied"
                )
                aggregated = _merge_metadata(aggregated, parsed)
                successful_chunks += 1
                continue
        
        if parse_reason == "json_repaired" or parse_reason == "json_repaired_prose_stripped":
            _log_failure_reason(
                parse_reason,
                f"{pdf_path.name} chunk {idx}/{len(chunks)}"
            )

        aggregated = _merge_metadata(aggregated, parsed)
        successful_chunks += 1

    if successful_chunks == 0 or not aggregated:
        _log_failure_reason("fallback_metadata", f"{pdf_path.name}: no valid chunk metadata")
        return _build_fallback_metadata(pdf_path, priority)

    aggregated = _enrich_beginner_foundation_metadata(aggregated)

    if str(aggregated.get("document_type", "")).strip().lower() not in VALID_DOCUMENT_TYPES:
        aggregated["document_type"] = "other"

    return aggregated


# ─────────────────────────────────────────

# MARKDOWN NOTE BUILDER

# ─────────────────────────────────────────

def build_markdown(paper_id: str, data: dict, priority: str, pdf_rel_path: str) -> str:
    authors   = ", ".join(data.get("authors", ["Unknown"]))
    year      = data.get("year", "Unknown")
    tags_str  = ", ".join(data.get("tags", []))
    related   = "\n".join(f"- {r}" for r in data.get("related_papers", [])) or "- None yet"
    features  = ", ".join(data.get("linked_features", []))

    def bullet_list(items) -> str:
        if isinstance(items, list):
            return "\n".join(f"- {i}" for i in items if str(i).strip()) or "- None extracted."
        return f"- {items}" if items else "- None extracted."

    return f"""---
paper_id: {paper_id}
title: "{data.get('title', '')}"
authors: [{authors}]
year: {year}
domain: {data.get('domain', '')}
sub_topic: {data.get('sub_topic', '')}
evidence_type: {data.get('evidence_type', '')}
document_type: {data.get('document_type', '')}
doc_type_confidence: {data.get('doc_type_confidence', '')}
source_priority: {priority}
evidence_strength: {data.get('evidence_strength', '')}
confidence_ceiling: {data.get('confidence_ceiling', '')}
completeness_score: {data.get('completeness_score', '')}
actionability_score: {data.get('actionability_score', '')}
cycling_specificity: {data.get('cycling_specificity', '')}
elite_applicability: {data.get('elite_applicability', '')}
resource_level: {data.get('resource_level', '')}
female_physiology_relevant: {data.get('female_physiology_relevant', 'no')}
altitude_heat_relevant: {data.get('altitude_heat_relevant', 'no')}
youth_applicable: {data.get('youth_applicable', 'no')}
masters_applicable: {data.get('masters_applicable', 'no')}
durability_relevant: {data.get('durability_relevant', 'no')}
linked_features: [{features}]
tags: [{tags_str}]
date_added: {TODAY}
---

# {data.get('title', 'Untitled')}

**Authors:** {authors}
**Year:** {year}
**Source:** {data.get('source', 'Unknown')}
**Evidence Type:** {data.get('evidence_type', '')}
**Document Type:** {data.get('document_type', '')} ({data.get('doc_type_confidence', '')})
**Source Priority:** {priority}
**Evidence Strength:** {data.get('evidence_strength', '')}/5  |  **Confidence Ceiling (no devices):** {data.get('confidence_ceiling', '')}/5
**Population:** {data.get('population', '')} | **Sample Size:** {data.get('sample_size', '')}
**Training Level:** {data.get('training_level', '')}

---

## Main Findings
{data.get('main_finding', 'Not extracted.')}

---

## Practical Application
{data.get('practical_application', 'Not extracted.')}

---

## Low Resource Applicability
{data.get('low_resource_applicability', 'Not specified. Review manually.')}

---

## Coaching Knowledge Nodes

### Coaching Principles
{bullet_list(data.get('coaching_principles', []))}

### Constraints (Must Not Violate)
{bullet_list(data.get('constraints', []))}

### Decision Rules (IF-THEN)
{bullet_list(data.get('decision_rules', []))}

### Individualization Factors
{bullet_list(data.get('individualization_factors', []))}

### Recovery Heuristics
{bullet_list(data.get('recovery_heuristics', []))}

---

## Applicability Flags
| Flag | Value |
|------|-------|
| Female Physiology | {data.get('female_physiology_relevant', 'no')} |
| Altitude / Heat | {data.get('altitude_heat_relevant', 'no')} |
| Youth | {data.get('youth_applicable', 'no')} |
| Masters (35+) | {data.get('masters_applicable', 'no')} |
| Durability / Multi-day | {data.get('durability_relevant', 'no')} |

---

## Linked Alpine Features
{features if features else 'None tagged.'}

---

## Related Papers
{related}

---

## PDF Location
{pdf_rel_path}

---

*Extraction confidence: {data.get('extraction_confidence', 'N/A')} | Added: {TODAY}*
"""


# ─────────────────────────────────────────

# CORE PROCESSOR

# ─────────────────────────────────────────

def process_pdf(pdf_path: Path, existing_rows: list, id_floor: int = 0) -> dict | None:

    if not pdf_path.exists():
        _log_failure_reason("stale_file_reference", f"{pdf_path.name}: file missing before processing")
        return None

    # Duplicate check
    if any(r.get("PDF_Filename", "").endswith(pdf_path.name) for r in existing_rows):
        print(f"  Already indexed, skipping: {pdf_path.name}")
        return None

    print(f"\nProcessing: {pdf_path.name}")

    # Extract text
    if is_scanned(pdf_path):
        print("  Scanned PDF detected, running OCR...")
        text = extract_text_ocr(pdf_path)
    else:
        print("  Text-based PDF, extracting directly...")
        text = extract_text_direct(pdf_path)

    if not text.strip():
        print("  No text extracted, skipping.")
        return None

    # Detect priority
    priority = detect_priority(text, pdf_path.name)
    print(f"  Source priority: {priority}")

    # LLM extraction
    data = extract_metadata(text, priority, pdf_path)

    # Safety overrides
    data["source_priority"] = priority
    if priority == "book":
        try:
            strength = int(float(data.get("evidence_strength", 3)))
        except (TypeError, ValueError):
            strength = 3
        data["evidence_strength"] = min(strength, 3)

    # Document type: strict rule-based override + confidence flag.
    doc_type, doc_type_confidence, doc_type_reason = classify_document_type(text, data, pdf_path.name)
    data["document_type"] = doc_type
    data["doc_type_confidence"] = doc_type_confidence

    # Extraction quality validation: catch placeholder leaks, padded list
    # items, and fabricated author names before they get written to the
    # index looking like legitimate content.
    quality_issues = detect_extraction_quality_issues(data, pdf_path)
    for issue in quality_issues:
        _log_failure_reason("extraction_quality_issue", f"{pdf_path.name}: {issue}")

    manual_review_required = "yes" if (doc_type_confidence == "low" or quality_issues) else "no"
    if doc_type_confidence == "low":
        _log_failure_reason("doc_type_low_confidence", f"{pdf_path.name}: {doc_type_reason}")

    # Resolve folders
    domain    = clean_folder_name(data.get("domain", "General"))
    sub_topic = clean_folder_name(data.get("sub_topic", "General"))
    branch    = "Journals" if priority == "journal" else "Books"

    dest_folder = PROCESSED_ROOT / branch / domain
    dest_folder.mkdir(parents=True, exist_ok=True)

    # Move PDF
    dest_pdf = dest_folder / pdf_path.name
    shutil.move(str(pdf_path), str(dest_pdf))
    pdf_rel_path = str(dest_pdf.relative_to(PROCESSED_ROOT))
    print(f"  PDF moved to: PDF Processed/{pdf_rel_path}")

    # Generate Paper ID
    paper_id = generate_paper_id(existing_rows, id_floor)

    # Save Markdown note
    notes_folder = NOTES_ROOT / domain / sub_topic
    notes_folder.mkdir(parents=True, exist_ok=True)

    note_name    = f"{data.get('year', 'XXXX')}_{clean_folder_name(data.get('title', pdf_path.stem)[:50])}.md"
    note_path    = notes_folder / note_name
    note_content = build_markdown(paper_id, data, priority, pdf_rel_path)

    with open(note_path, "w", encoding="utf-8") as f:
        f.write(note_content)
    print(f"  Note saved: Notes/{domain}/{sub_topic}/{note_name}")

    obsidian_path = str(note_path.relative_to(NOTES_ROOT))

    def _pipe_join(val) -> str:
        """Join a list with ' | ' or return the raw string value."""
        if isinstance(val, list):
            return " | ".join(str(v).strip() for v in val if str(v).strip())
        return str(val) if val else ""

    # Build CSV row
    row = {
        # ── Identity ──────────────────────────────────────────────────────────
        "Paper_ID":                   paper_id,
        "Title":                      data.get("title", ""),
        "Authors":                    "; ".join(data.get("authors", [])),
        "Year":                       data.get("year", ""),
        "Domain":                     domain,
        "Sub_Topic":                  sub_topic,
        "Evidence_Type":              data.get("evidence_type", ""),
        "Document_Type":              data.get("document_type", "other"),
        "Doc_Type_Confidence":        data.get("doc_type_confidence", "low"),
        "Manual_Review_Required":     manual_review_required,
        "Evidence_Score":             data.get("evidence_strength", ""),
        "Source_Priority":            priority,
        "Source":                     data.get("source", ""),

        # ── Applicability flags ───────────────────────────────────────────────
        "Cycling_Specificity":        data.get("cycling_specificity", ""),
        "Elite_Applicability":        data.get("elite_applicability", ""),
        "Resource_Level":             data.get("resource_level", ""),
        "Female_Physiology_Relevant": data.get("female_physiology_relevant", "no"),
        "Altitude_Heat_Relevant":     data.get("altitude_heat_relevant", "no"),
        "Youth_Applicable":           data.get("youth_applicable", "no"),
        "Masters_Applicable":         data.get("masters_applicable", "no"),
        "Durability_Relevant":        data.get("durability_relevant", "no"),

        # ── Core content ──────────────────────────────────────────────────────
        "Main_Finding":               data.get("main_finding", ""),
        "Practical_Application":      data.get("practical_application", ""),
        "Low_Resource_Applicability": data.get("low_resource_applicability", ""),

        # ── Coaching knowledge nodes ──────────────────────────────────────────
        "Coaching_Principles":        _pipe_join(data.get("coaching_principles", [])),
        "Constraints":                _pipe_join(data.get("constraints", [])),
        "Decision_Rules":             _pipe_join(data.get("decision_rules", [])),
        "Individualization_Factors":  _pipe_join(data.get("individualization_factors", [])),
        "Recovery_Heuristics":        _pipe_join(data.get("recovery_heuristics", [])),

        # ── Governance ────────────────────────────────────────────────────────
        "Superseded_By":              data.get("superseded_by", ""),
        "Confidence_Ceiling":         data.get("confidence_ceiling", data.get("evidence_strength", "")),

        # ── Search & linkage ──────────────────────────────────────────────────
        "Tags":                       ", ".join(data.get("tags", [])),
        "Related_Papers":             "; ".join(data.get("related_papers", [])),
        "Linked_Features":            ", ".join(data.get("linked_features", [])),

        # ── Provenance ────────────────────────────────────────────────────────
        "PDF_Filename":               pdf_rel_path,
        "Obsidian_Path":              obsidian_path,
        "Acquisition_Status":         "Extracted & Organized",
        "Date_Added":                 TODAY
    }

    if manual_review_required == "yes":
        reasons = list(quality_issues)
        if doc_type_confidence == "low":
            reasons.append(doc_type_reason)
        save_manual_review_queue(
            [
                {
                    "Paper_ID": row.get("Paper_ID", ""),
                    "Title": row.get("Title", ""),
                    "Year": row.get("Year", ""),
                    "Document_Type": row.get("Document_Type", ""),
                    "Doc_Type_Confidence": row.get("Doc_Type_Confidence", ""),
                    "Manual_Review_Reason": "; ".join(reasons) if reasons else "unspecified",
                    "Source": row.get("Source", ""),
                    "Evidence_Type": row.get("Evidence_Type", ""),
                    "PDF_Filename": row.get("PDF_Filename", ""),
                    "Date_Added": row.get("Date_Added", TODAY),
                }
            ]
        )

    return row


# ─────────────────────────────────────────

# MAIN RUNNER

# ─────────────────────────────────────────

def main():
    RAW_FOLDER.mkdir(exist_ok=True)
    PROCESSED_ROOT.mkdir(exist_ok=True)
    NOTES_ROOT.mkdir(exist_ok=True)

    existing_rows = load_index()
    id_floor = paper_id_floor()

    # Backfill pass for existing rows: rule-based type correction without PDF re-parse.
    existing_rows, backfill_review_rows, changed = reclassify_existing_rows(existing_rows)
    if changed > 0:
        print(f"Backfill reclassification updated {changed} existing row(s).")
        save_index(existing_rows)
    if backfill_review_rows:
        save_manual_review_queue(backfill_review_rows)
        print(f"Backfill queued {len(backfill_review_rows)} low-confidence row(s) for manual review.")
    new_rows      = []

    pdfs = list(RAW_FOLDER.glob("*.pdf"))

    if not pdfs:
        print("No PDFs found in 'PDF RAW/'. Drop some PDFs in and run again.")
        return

    print(f"Found {len(pdfs)} PDF(s) to process.\n")

    for pdf in pdfs:
        try:
            row = process_pdf(pdf, existing_rows, id_floor)
            if row:
                existing_rows.append(row)
                new_rows.append(row)
        except Exception as e:
            _log_failure_reason("document_processing_error", f"{pdf.name}: {e}")
            continue

    if new_rows:
        save_index(existing_rows)
        print(f"\nDone. {len(new_rows)} new record(s) added to Master_Index.csv")
    else:
        print("\nNo new records added.")


if __name__ == "__main__":
    main()
