"""
import_pdfs.py — AlpineAi_Library pipeline entry point

Drop PDFs into:  data/raw/PDF_RAW/
Run:             python scripts/import_pdfs.py

The script will:
  1. Scan for new PDFs in PDF_RAW/
  2. Extract text (direct or OCR for scanned PDFs)
  3. Send each document to the LLM for metadata extraction
  4. Write structured rows to data/indexes/Master_Index.csv
  5. Save rich Markdown notes to data/processed/notes/
  6. Move processed PDFs into data/processed/PDF_PROCESSED/<domain>/
  7. Flag low-confidence documents in Manual_Review_Queue.csv

Requires:
  - ALPIE_API_KEY environment variable set
  - pip install pymupdf pi169
  - Optional OCR: pip install pytesseract Pillow  +  tesseract installed
"""

import sys
from pathlib import Path

# Allow running from repo root or scripts/ folder
SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT    = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# ── import the full extractor ───────────────────────────────────────────────
# alpine_extract_to_master_index lives in scripts/ alongside this file.
# All heavy lifting (LLM extraction, CSV writing, note generation) lives there.
try:
    from alpine_extract_to_master_index import (
        RAW_FOLDER,
        PROCESSED_ROOT,
        NOTES_ROOT,
        INDEX_FILE,
        MANUAL_REVIEW_FILE,
        load_index,
        save_index,
        save_manual_review_queue,
        reclassify_existing_rows,
        paper_id_floor,
        process_pdf,
    )
except ImportError as exc:
    print(f"ERROR: could not import extractor — {exc}")
    print("Make sure alpine_extract_to_master_index.py is in the scripts/ folder")
    print("and that PyMuPDF and pi169 are installed:")
    print("  pip install pymupdf pi169")
    sys.exit(1)


# ── optional progress bar ───────────────────────────────────────────────────
try:
    from tqdm import tqdm
    def _progress(iterable, **kw):
        return tqdm(iterable, **kw)
except ImportError:
    def _progress(iterable, **kw):
        return iterable


def ensure_dirs():
    for path in [RAW_FOLDER, PROCESSED_ROOT, NOTES_ROOT, INDEX_FILE.parent,
                 MANUAL_REVIEW_FILE.parent]:
        path.mkdir(parents=True, exist_ok=True)


def collect_pdfs() -> list[Path]:
    """Return all PDFs in PDF_RAW/, sorted for deterministic ordering."""
    if not RAW_FOLDER.exists():
        return []
    return sorted(RAW_FOLDER.rglob("*.pdf"))


def main():
    import os
    if not os.environ.get("ALPIE_API_KEY"):
        print("ERROR: ALPIE_API_KEY environment variable is not set.")
        print("Set it before running:  set ALPIE_API_KEY=<your-key>  (Windows)")
        sys.exit(1)

    ensure_dirs()

    existing_rows = load_index()
    print(f"Existing index entries: {len(existing_rows)}")

    # Durable high-water mark for Paper_ID numbering, independent of the
    # current (possibly-trimmed) Master_Index.csv — prevents ID reuse when
    # rows have been removed (e.g. by clear_for_reprocess.py) but the ID
    # still shows up in old notes or the manual review queue.
    id_floor = paper_id_floor()

    # Backfill: re-classify document types for already-indexed rows (no LLM call)
    existing_rows, backfill_review, changed = reclassify_existing_rows(existing_rows)
    if changed:
        print(f"Backfill reclassification updated {changed} existing row(s).")
        save_index(existing_rows)
    if backfill_review:
        save_manual_review_queue(backfill_review)

    # Discover new PDFs
    pdfs = collect_pdfs()
    if not pdfs:
        print(f"\nNo PDFs found in: {RAW_FOLDER}")
        print("Drop PDF files there and re-run.")
        return

    print(f"\nFound {len(pdfs)} PDF(s) to process.\n")

    new_rows = []
    review_rows = []
    errors = []

    for pdf_path in _progress(pdfs, desc="Processing PDFs", unit="pdf"):
        try:
            row = process_pdf(pdf_path, existing_rows + new_rows, id_floor)
            if row is None:
                continue  # already indexed or no text

            new_rows.append(row)
            if row.get("Manual_Review_Required") == "yes":
                review_rows.append({
                    "Paper_ID":            row["Paper_ID"],
                    "Title":               row["Title"],
                    "Year":                row["Year"],
                    "Document_Type":       row["Document_Type"],
                    "Doc_Type_Confidence": row["Doc_Type_Confidence"],
                    "Manual_Review_Reason": "low_doc_type_confidence",
                    "Source":              row["Source"],
                    "Evidence_Type":       row["Evidence_Type"],
                    "PDF_Filename":        row["PDF_Filename"],
                    "Date_Added":          row["Date_Added"],
                })

        except Exception as exc:
            errors.append((pdf_path.name, str(exc)))
            print(f"\n  ERROR processing {pdf_path.name}: {exc}")

    # Persist
    if new_rows:
        all_rows = existing_rows + new_rows
        save_index(all_rows)
        print(f"\n✓ Indexed {len(new_rows)} new document(s).")
        print(f"  Master_Index.csv now has {len(all_rows)} entries.")

    if review_rows:
        save_manual_review_queue(review_rows)
        print(f"  {len(review_rows)} document(s) flagged for manual review.")

    if errors:
        print(f"\n⚠  {len(errors)} document(s) failed:")
        for name, msg in errors:
            print(f"   • {name}: {msg}")

    if not new_rows and not errors:
        print("Nothing new to index.")

    print("\nDone.")


if __name__ == "__main__":
    main()

