#!/usr/bin/env python3
"""
Backfill beginner/foundational metadata into the existing Master_Index.csv.
"""

import csv
import importlib.util
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
INDEX_FILE = BASE_DIR / "data" / "indexes" / "Master_Index.csv"
EXTRACTOR_FILE = Path(__file__).resolve().parent / "alpine_extract_to_master_index.py"


def _load_extractor_module():
    spec = importlib.util.spec_from_file_location("alpine_extract_to_master_index", EXTRACTOR_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _split_pipe_list(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split("|") if item.strip()]


def _split_csv_list(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _join_pipe(values: list[str]) -> str:
    seen = set()
    ordered = []
    for value in values:
        token = str(value).strip()
        if token and token not in seen:
            seen.add(token)
            ordered.append(token)
    return " | ".join(ordered)


def _join_csv(values: list[str]) -> str:
    seen = set()
    ordered = []
    for value in values:
        token = str(value).strip()
        if token and token not in seen:
            seen.add(token)
            ordered.append(token)
    return ", ".join(ordered)


def main():
    extractor = _load_extractor_module()

    with open(INDEX_FILE, newline="", encoding="utf-8", errors="ignore") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = rows[0].keys() if rows else []

    updated = 0
    for row in rows:
        payload = {
            "title": row.get("Title", ""),
            "domain": row.get("Domain", ""),
            "sub_topic": row.get("Sub_Topic", ""),
            "main_finding": row.get("Main_Finding", ""),
            "practical_application": row.get("Practical_Application", ""),
            "low_resource_applicability": row.get("Low_Resource_Applicability", ""),
            "coaching_principles": _split_pipe_list(row.get("Coaching_Principles", "")),
            "decision_rules": _split_pipe_list(row.get("Decision_Rules", "")),
            "constraints": _split_pipe_list(row.get("Constraints", "")),
            "recovery_heuristics": _split_pipe_list(row.get("Recovery_Heuristics", "")),
            "tags": _split_csv_list(row.get("Tags", "")),
            "training_level": row.get("Training_Level", ""),
            "population": row.get("Population", ""),
        }
        enriched = extractor._enrich_beginner_foundation_metadata(payload)

        changed = False
        mapping = {
            "Domain": enriched.get("domain", row.get("Domain", "")),
            "Sub_Topic": enriched.get("sub_topic", row.get("Sub_Topic", "")),
            "Practical_Application": enriched.get("practical_application", row.get("Practical_Application", "")),
            "Low_Resource_Applicability": enriched.get("low_resource_applicability", row.get("Low_Resource_Applicability", "")),
            "Coaching_Principles": _join_pipe(enriched.get("coaching_principles", [])),
            "Decision_Rules": _join_pipe(enriched.get("decision_rules", [])),
            "Constraints": _join_pipe(enriched.get("constraints", [])),
            "Recovery_Heuristics": _join_pipe(enriched.get("recovery_heuristics", [])),
            "Tags": _join_csv(enriched.get("tags", [])),
        }

        for key, value in mapping.items():
            if row.get(key, "") != value:
                row[key] = value
                changed = True

        if changed:
            updated += 1

    with open(INDEX_FILE, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated beginner/foundation metadata on {updated} rows.")


if __name__ == "__main__":
    main()
