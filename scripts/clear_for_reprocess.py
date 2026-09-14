#!/usr/bin/env python3
"""
Clear one or more Paper_IDs (or everything currently in
Manual_Review_Queue.csv) out of Master_Index.csv and
Manual_Review_Queue.csv, and retire their note files, so the source PDF
can be dropped back into data/raw/PDF_RAW/ and actually get reprocessed.

Without this step, process_pdf()'s duplicate check skips any PDF whose
filename already matches a PDF_Filename in Master_Index.csv - so a
flagged-but-still-indexed row silently blocks reprocessing even after
it's in the manual review queue.

This script only touches local files (Master_Index.csv,
Manual_Review_Queue.csv, and the note file). It does not touch Supabase -
at the end it prints a DELETE statement for library_chunks that you can
run yourself in the Supabase SQL editor.

Usage:
    python scripts/clear_for_reprocess.py ALP-2026-0018 ALP-2026-0313
    python scripts/clear_for_reprocess.py --all-queued
    python scripts/clear_for_reprocess.py --all-queued --dry-run
"""
import argparse
import csv
import shutil
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INDEX_FILE = BASE_DIR / "data" / "indexes" / "Master_Index.csv"
QUEUE_FILE = BASE_DIR / "data" / "indexes" / "Manual_Review_Queue.csv"
NOTES_ROOT = BASE_DIR / "data" / "processed" / "notes"
PROCESSED_ROOT = BASE_DIR / "data" / "processed" / "PDF_PROCESSED"
RETIRED_NOTES_DIR = NOTES_ROOT / "_retired_pending_reprocessing"
BACKUP_ROOT = BASE_DIR / "data" / "indexes"


def load_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def save_csv(path, fieldnames, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def backup(paths, label):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_ROOT / f"_backups_{label}_{ts}"
    dest.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if p.exists():
            shutil.copy2(p, dest / (p.name + ".bak"))
    return dest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paper_ids", nargs="*", help="Paper_IDs to clear, e.g. ALP-2026-0018")
    parser.add_argument("--all-queued", action="store_true", help="Clear every Paper_ID currently in Manual_Review_Queue.csv")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without changing anything")
    args = parser.parse_args()

    idx_fields, idx_rows = load_csv(INDEX_FILE)
    idx_by_id = {r["Paper_ID"]: r for r in idx_rows}

    queue_fields, queue_rows = load_csv(QUEUE_FILE)
    queue_ids = {r["Paper_ID"] for r in queue_rows}

    if args.all_queued:
        targets = sorted(queue_ids)
    else:
        targets = args.paper_ids

    if not targets:
        print("No Paper_IDs given. Pass one or more IDs, or --all-queued.")
        return

    missing = [t for t in targets if t not in idx_by_id]
    targets = [t for t in targets if t in idx_by_id]
    if missing:
        print(f"Not in Master_Index.csv (already cleared, or never existed): {missing}")

    if not targets:
        print("Nothing to clear.")
        return

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Clearing {len(targets)} row(s): {targets}\n")

    if not args.dry_run:
        backup_dir = backup([INDEX_FILE, QUEUE_FILE], "clear_for_reprocess")
        print(f"Backed up Master_Index.csv and Manual_Review_Queue.csv to {backup_dir}\n")

    results = []
    for pid in targets:
        row = idx_by_id[pid]
        pdf_filename = row.get("PDF_Filename", "")
        obsidian_path = row.get("Obsidian_Path", "")
        note_path = NOTES_ROOT / obsidian_path if obsidian_path else None
        pdf_path = PROCESSED_ROOT / pdf_filename if pdf_filename else None

        results.append({
            "paper_id": pid,
            "title": row.get("Title", ""),
            "pdf_path": str(pdf_path) if pdf_path else "(none recorded)",
            "pdf_exists": bool(pdf_path and pdf_path.exists()),
        })

        if args.dry_run:
            continue

        if note_path and note_path.exists():
            RETIRED_NOTES_DIR.mkdir(parents=True, exist_ok=True)
            safe_name = obsidian_path.replace("/", "_").replace("\\", "_")
            dest = RETIRED_NOTES_DIR / safe_name
            if dest.exists():
                dest = RETIRED_NOTES_DIR / f"{pid}_{safe_name}"
            shutil.move(str(note_path), str(dest))

    if not args.dry_run:
        target_set = set(targets)
        idx_rows = [r for r in idx_rows if r["Paper_ID"] not in target_set]
        save_csv(INDEX_FILE, idx_fields, idx_rows)

        queue_rows = [r for r in queue_rows if r["Paper_ID"] not in target_set]
        save_csv(QUEUE_FILE, queue_fields, queue_rows)

    print("Summary:")
    for r in results:
        if r["pdf_exists"]:
            status = "PDF found -> move this file into data/raw/PDF_RAW/ to reprocess"
        else:
            status = "PDF NOT FOUND on disk -> source file is missing, re-acquire it before reprocessing"
        print(f"  {r['paper_id']} | {r['title'][:60]}")
        print(f"    {status}")
        print(f"    {r['pdf_path']}")

    if not args.dry_run and targets:
        ids_sql = ", ".join(f"'{t}'" for t in targets)
        print("\nThis script does not touch Supabase. Run this in the Supabase SQL editor")
        print("to remove these rows from library_chunks:\n")
        print(f"  delete from public.library_chunks where source_id in ({ids_sql});")


if __name__ == "__main__":
    main()
