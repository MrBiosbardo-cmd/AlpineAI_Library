#!/usr/bin/env python3
"""
Coaching Rule Engine: converts extracted Master_Index coaching fields into live rules.
Handles rule selection, conflict resolution, and athlete context mapping.
"""

import csv
import sqlite3
from datetime import datetime
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MASTER_INDEX = BASE_DIR / "data" / "indexes" / "Master_Index.csv"


def _split_pipe_list(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split("|") if item.strip()]


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tokenize_text(value: str) -> set[str]:
    return {
        token
        for token in str(value or "").lower().replace("/", " ").replace("-", " ").replace("_", " ").split()
        if len(token) > 2
    }


class CoachingRuleEngine:
    """Loads coaching intelligence from Master_Index.csv and exposes live rule selection."""

    def __init__(self, db_path=":memory:"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.setup_schema()

    def setup_schema(self):
        """Create rule engine tables."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS coaching_rules (
                rule_id TEXT PRIMARY KEY,
                node_id TEXT,
                source_paper_id TEXT,
                rule_type TEXT,
                domain TEXT,
                condition TEXT,
                action TEXT,
                constraint_level TEXT,
                precedence INTEGER,
                confidence_score REAL,
                applies_to_contexts TEXT,
                female_relevant TEXT,
                youth_applicable TEXT,
                masters_applicable TEXT,
                altitude_heat_relevant TEXT,
                durability_relevant TEXT,
                resource_level TEXT,
                confidence_ceiling REAL,
                source_excerpt TEXT,
                date_created TEXT
            );

            CREATE TABLE IF NOT EXISTS rule_conflicts (
                conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id_1 TEXT,
                rule_id_2 TEXT,
                conflict_type TEXT,
                resolution_strategy TEXT,
                date_flagged TEXT
            );

            CREATE TABLE IF NOT EXISTS athlete_context (
                context_id TEXT PRIMARY KEY,
                athlete_id TEXT,
                sex TEXT,
                age INTEGER,
                maturity_stage TEXT,
                competitive_level TEXT,
                injury_status TEXT,
                recovery_capacity TEXT,
                constraints TEXT,
                date_created TEXT
            );

            CREATE TABLE IF NOT EXISTS rule_application_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id TEXT,
                context_id TEXT,
                decision TEXT,
                rationale TEXT,
                applied BOOLEAN,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

    def load_rules_from_index(self):
        """Load extracted coaching fields from Master_Index.csv into live rules."""
        rules_created = 0

        with open(MASTER_INDEX, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                paper_id = row.get("Paper_ID", "").strip('"')
                if not paper_id:
                    continue

                domain = row.get("Domain", "").strip('"')
                sub_topic = row.get("Sub_Topic", "").strip('"')
                evidence_score = _safe_float(row.get("Evidence_Score"), 0.0)
                confidence_ceiling = _safe_float(row.get("Confidence_Ceiling"), evidence_score or 0.0)
                confidence_score = min(max(evidence_score / 5.0, 0.1), 1.0)
                low_resource = row.get("Low_Resource_Applicability", "").strip('"')
                applies_to_contexts = self._build_contexts(row)

                rules_created += self._insert_rules(
                    paper_id=paper_id,
                    domain=domain,
                    sub_topic=sub_topic,
                    row=row,
                    field_name="Constraints",
                    rule_type="constraint",
                    constraint_level="must",
                    precedence=1,
                    confidence_score=confidence_score,
                    applies_to_contexts=applies_to_contexts,
                    source_excerpt=low_resource or row.get("Main_Finding", "").strip('"'),
                )
                rules_created += self._insert_rules(
                    paper_id=paper_id,
                    domain=domain,
                    sub_topic=sub_topic,
                    row=row,
                    field_name="Decision_Rules",
                    rule_type="decision",
                    constraint_level="should",
                    precedence=2,
                    confidence_score=confidence_score,
                    applies_to_contexts=applies_to_contexts,
                    source_excerpt=row.get("Practical_Application", "").strip('"'),
                )
                rules_created += self._insert_rules(
                    paper_id=paper_id,
                    domain=domain,
                    sub_topic=sub_topic,
                    row=row,
                    field_name="Coaching_Principles",
                    rule_type="principle",
                    constraint_level="should",
                    precedence=3,
                    confidence_score=confidence_score,
                    applies_to_contexts=applies_to_contexts,
                    source_excerpt=row.get("Main_Finding", "").strip('"'),
                )
                rules_created += self._insert_rules(
                    paper_id=paper_id,
                    domain=domain,
                    sub_topic=sub_topic,
                    row=row,
                    field_name="Individualization_Factors",
                    rule_type="contextual",
                    constraint_level="consider",
                    precedence=4,
                    confidence_score=min(confidence_score, max(confidence_ceiling / 5.0, 0.1)),
                    applies_to_contexts=applies_to_contexts,
                    source_excerpt=low_resource,
                )
                rules_created += self._insert_rules(
                    paper_id=paper_id,
                    domain=domain,
                    sub_topic=sub_topic,
                    row=row,
                    field_name="Recovery_Heuristics",
                    rule_type="heuristic",
                    constraint_level="may",
                    precedence=5,
                    confidence_score=confidence_score,
                    applies_to_contexts=applies_to_contexts,
                    source_excerpt=row.get("Practical_Application", "").strip('"'),
                )

        self.conn.commit()
        return rules_created

    def _build_contexts(self, row: dict) -> str:
        contexts = ["all_contexts"]
        if row.get("Female_Physiology_Relevant", "no").strip().lower() in {"yes", "partial"}:
            contexts.append("female")
        if row.get("Youth_Applicable", "no").strip().lower() in {"yes", "partial"}:
            contexts.append("youth")
        if row.get("Masters_Applicable", "no").strip().lower() in {"yes", "partial"}:
            contexts.append("masters")
        if row.get("Altitude_Heat_Relevant", "no").strip().lower() in {"yes", "partial"}:
            contexts.append("heat_altitude")
        if row.get("Durability_Relevant", "no").strip().lower() == "yes":
            contexts.append("durability")
        contexts.append(row.get("Resource_Level", "Low").strip('"').lower() or "low")
        return ",".join(dict.fromkeys(contexts))

    def _insert_rules(
        self,
        *,
        paper_id: str,
        domain: str,
        sub_topic: str,
        row: dict,
        field_name: str,
        rule_type: str,
        constraint_level: str,
        precedence: int,
        confidence_score: float,
        applies_to_contexts: str,
        source_excerpt: str,
    ) -> int:
        created = 0
        actions = _split_pipe_list(row.get(field_name, ""))
        for index, action in enumerate(actions, start=1):
            rule_id = f"{paper_id}-{rule_type}-{index}"
            condition = self._build_condition(rule_type, domain, sub_topic, row)
            self.conn.execute("""
                INSERT OR REPLACE INTO coaching_rules (
                    rule_id, node_id, source_paper_id, rule_type, domain,
                    condition, action, constraint_level, precedence, confidence_score,
                    applies_to_contexts, female_relevant, youth_applicable, masters_applicable,
                    altitude_heat_relevant, durability_relevant, resource_level, confidence_ceiling,
                    source_excerpt, date_created
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rule_id,
                rule_id,
                paper_id,
                rule_type,
                domain,
                condition,
                action,
                constraint_level,
                precedence,
                confidence_score,
                applies_to_contexts,
                row.get("Female_Physiology_Relevant", "no").strip().lower(),
                row.get("Youth_Applicable", "no").strip().lower(),
                row.get("Masters_Applicable", "no").strip().lower(),
                row.get("Altitude_Heat_Relevant", "no").strip().lower(),
                row.get("Durability_Relevant", "no").strip().lower(),
                row.get("Resource_Level", "low").strip().lower(),
                _safe_float(row.get("Confidence_Ceiling"), confidence_score * 5.0),
                source_excerpt,
                datetime.now().isoformat(),
            ))
            created += 1
        return created

    def _build_condition(self, rule_type: str, domain: str, sub_topic: str, row: dict) -> str:
        parts = [f"domain={domain}" if domain else "", f"sub_topic={sub_topic}" if sub_topic else ""]
        if rule_type == "contextual":
            parts.append(f"population={row.get('Population', '').strip()}")
        if row.get("Female_Physiology_Relevant", "no").strip().lower() in {"yes", "partial"}:
            parts.append("sex=female_relevant")
        if row.get("Durability_Relevant", "no").strip().lower() == "yes":
            parts.append("durability=yes")
        return "; ".join(part for part in parts if part) or "general_context"

    def detect_conflicts(self):
        """Detect likely duplicate or opposing hard constraints within the same domain."""
        conflicts = []
        seen_pairs = set()
        cursor = self.conn.execute("""
            SELECT rule_id, domain, action, constraint_level, precedence
            FROM coaching_rules
            ORDER BY precedence ASC
        """)
        rules = cursor.fetchall()

        domain_rules = defaultdict(list)
        for rule in rules:
            domain_rules[rule["domain"]].append(rule)

        for domain, domain_rule_list in domain_rules.items():
            must_rules = [rule for rule in domain_rule_list if rule["constraint_level"] == "must"]
            if len(must_rules) < 2:
                continue
            for i, rule1 in enumerate(must_rules):
                for rule2 in must_rules[i + 1:]:
                    action1 = str(rule1["action"] or "").strip().lower()
                    action2 = str(rule2["action"] or "").strip().lower()
                    if not action1 or not action2:
                        continue
                    if action1 == action2:
                        conflict_type = "duplicate_constraint"
                    elif self._actions_overlap(action1, action2):
                        conflict_type = "overlapping_constraints"
                    else:
                        continue
                    pair_key = tuple(sorted((rule1["rule_id"], rule2["rule_id"])))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    self.conn.execute("""
                        INSERT INTO rule_conflicts (
                            rule_id_1, rule_id_2, conflict_type, resolution_strategy, date_flagged
                        ) VALUES (?, ?, ?, ?, ?)
                    """, (
                        rule1["rule_id"],
                        rule2["rule_id"],
                        conflict_type,
                        "Review both constraints and prefer the higher-evidence, more specific rule.",
                        datetime.now().isoformat(),
                    ))
                    conflicts.append((rule1["rule_id"], rule2["rule_id"]))

        self.conn.commit()
        return len(conflicts)

    def _actions_overlap(self, action1: str, action2: str) -> bool:
        tokens1 = {token for token in action1.replace(",", " ").split() if len(token) > 3}
        tokens2 = {token for token in action2.replace(",", " ").split() if len(token) > 3}
        if not tokens1 or not tokens2:
            return False
        overlap = tokens1.intersection(tokens2)
        return len(overlap) >= 4

    def add_athlete_context(self, context_id, sex, age, competitive_level, injury_status, recovery_capacity):
        """Add an athlete context for rule application."""
        maturity = self._compute_maturity(age)
        self.conn.execute("""
            INSERT OR IGNORE INTO athlete_context (
                context_id, athlete_id, sex, age, maturity_stage,
                competitive_level, injury_status, recovery_capacity,
                date_created
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            context_id, context_id, sex, age, maturity,
            competitive_level, injury_status, recovery_capacity,
            datetime.now().isoformat()
        ))
        self.conn.commit()

    def _compute_maturity(self, age):
        if age is None:
            return "unknown"
        if age < 18:
            return "youth"
        if age < 24:
            return "junior"
        if age < 35:
            return "senior"
        return "masters"

    def select_rules_for_context(self, context_id, coaching_goal: str = "", target_domain: str = "", target_sub_topic: str = ""):
        """Select applicable rules for an athlete context and coaching problem."""
        context = self.conn.execute(
            "SELECT * FROM athlete_context WHERE context_id = ?",
            (context_id,)
        ).fetchone()

        if not context:
            return []

        context_tokens = {"all_contexts", context["competitive_level"].lower()}
        if context["sex"]:
            context_tokens.add(context["sex"].lower())
        if context["maturity_stage"]:
            context_tokens.add(context["maturity_stage"].lower())
        if context["injury_status"] and context["injury_status"] != "none":
            context_tokens.add("recovery")
        if context["recovery_capacity"]:
            context_tokens.add(context["recovery_capacity"].lower())

        goal_tokens = _tokenize_text(coaching_goal)
        target_domain = str(target_domain or "").strip().lower()
        target_sub_topic = str(target_sub_topic or "").strip().lower()

        rules = self.conn.execute("""
            SELECT rule_id, node_id, rule_type, action, precedence, constraint_level,
                   applies_to_contexts, confidence_score, domain, condition,
                   female_relevant, youth_applicable, masters_applicable,
                   altitude_heat_relevant, durability_relevant, resource_level, confidence_ceiling,
                   source_excerpt
            FROM coaching_rules
            ORDER BY precedence ASC, confidence_score DESC
        """).fetchall()

        result = []
        seen = set()
        for rule in rules:
            applies_to = {
                token.strip().lower()
                for token in str(rule["applies_to_contexts"] or "all_contexts").split(",")
                if token.strip()
            }
            if "all_contexts" not in applies_to and applies_to.isdisjoint(context_tokens):
                continue
            if context["sex"] != "female" and rule["female_relevant"] == "yes":
                continue
            if context["maturity_stage"] == "youth" and rule["youth_applicable"] == "no":
                continue
            if context["maturity_stage"] == "masters" and rule["masters_applicable"] == "no":
                continue
            if context["maturity_stage"] == "senior" and rule["youth_applicable"] == "yes":
                continue
            if context["injury_status"] != "none" and rule["rule_type"] == "decision" and rule["confidence_ceiling"] < 3:
                continue
            if context["recovery_capacity"] == "low" and rule["constraint_level"] == "may":
                continue
            rule_dict = dict(rule)
            if not self._matches_problem(rule_dict, goal_tokens, target_domain, target_sub_topic):
                continue
            if rule_dict["rule_id"] not in seen:
                result.append(rule_dict)
                seen.add(rule_dict["rule_id"])
        return result

    def _matches_problem(self, rule: dict, goal_tokens: set[str], target_domain: str, target_sub_topic: str) -> bool:
        domain = str(rule.get("domain") or "").strip().lower()
        condition = str(rule.get("condition") or "").strip().lower()
        action = str(rule.get("action") or "").strip().lower()
        excerpt = str(rule.get("source_excerpt") or "").strip().lower()
        domain_tokens = _tokenize_text(domain)
        condition_tokens = _tokenize_text(condition)

        if target_domain:
            requested_domain_tokens = _tokenize_text(target_domain)
            if requested_domain_tokens and requested_domain_tokens.isdisjoint(domain_tokens.union(condition_tokens)):
                return False
        if target_sub_topic and target_sub_topic.lower() not in condition:
            return False
        if not goal_tokens:
            return True

        searchable = _tokenize_text(" ".join([domain, condition, action, excerpt]))
        overlap = goal_tokens.intersection(searchable)
        return len(overlap) > 0

    def apply_rules(self, context_id, domain=None, coaching_goal: str = "", target_sub_topic: str = ""):
        """Apply rules to an athlete context and log decisions."""
        rules = self.select_rules_for_context(
            context_id,
            coaching_goal=coaching_goal,
            target_domain=domain or "",
            target_sub_topic=target_sub_topic,
        )

        decisions = []
        for rule in rules:
            applied = rule["constraint_level"] in ("must", "should")
            decision = f"{rule['rule_type'].upper()}: {rule['action']}"
            rationale = f"{rule['condition']} | confidence={rule['confidence_score']:.2f}"

            self.conn.execute("""
                INSERT INTO rule_application_log (
                    rule_id, context_id, decision, rationale, applied
                ) VALUES (?, ?, ?, ?, ?)
            """, (rule["rule_id"], context_id, decision, rationale, applied))

            decisions.append({
                "rule_id": rule["rule_id"],
                "decision": decision,
                "applied": applied,
            })

        self.conn.commit()
        return decisions

    def get_rule_stats(self):
        stats = {}
        cursor = self.conn.execute("""
            SELECT rule_type, COUNT(*) as count
            FROM coaching_rules
            GROUP BY rule_type
        """)
        stats["rules_by_type"] = dict(cursor.fetchall())

        cursor = self.conn.execute("""
            SELECT constraint_level, COUNT(*) as count
            FROM coaching_rules
            GROUP BY constraint_level
        """)
        stats["rules_by_constraint"] = dict(cursor.fetchall())

        cursor = self.conn.execute("SELECT COUNT(*) as count FROM rule_conflicts")
        stats["total_conflicts"] = cursor.fetchone()["count"]

        cursor = self.conn.execute("SELECT AVG(confidence_score) as avg FROM coaching_rules")
        stats["avg_confidence"] = cursor.fetchone()["avg"]

        return stats


def main():
    engine = CoachingRuleEngine()

    print("=== COACHING RULE ENGINE ===\n")

    print("1. Loading extracted coaching intelligence from Master_Index.csv...")
    rules_created = engine.load_rules_from_index()
    print(f"   Created {rules_created} coaching rules")

    print("\n2. Detecting rule conflicts...")
    conflicts = engine.detect_conflicts()
    print(f"   Detected {conflicts} potential conflicts")

    print("\n3. Rule Engine Statistics:")
    stats = engine.get_rule_stats()
    print("   Rules by type:")
    for rule_type, count in stats["rules_by_type"].items():
        print(f"     {rule_type}: {count}")
    print("   Rules by constraint level:")
    for level, count in stats["rules_by_constraint"].items():
        print(f"     {level}: {count}")
    print(f"   Total conflicts: {stats['total_conflicts']}")
    print(f"   Average confidence: {stats['avg_confidence']:.2f}" if stats["avg_confidence"] is not None else "   Average confidence: n/a")

    print("\n4. Creating sample athlete contexts...")
    contexts = [
        ("ATHLETE-001", "female", 24, "elite", "none", "high"),
        ("ATHLETE-002", "male", 19, "junior", "minor_injury", "medium"),
        ("ATHLETE-003", "female", 38, "competitive", "none", "medium"),
    ]
    for context_id, sex, age, level, injury, recovery in contexts:
        engine.add_athlete_context(context_id, sex, age, level, injury, recovery)
        print(f"   Added context {context_id} ({sex}, age {age}, {level})")

    print("\n5. Applying rules to athlete contexts...")
    demo_queries = [
        ("ATHLETE-001", "fatigue management and recovery week adjustment", "Recovery", ""),
        ("ATHLETE-002", "interval prescription for trained cyclists", "Training_Prescription", ""),
        ("ATHLETE-003", "female physiology and durability support", "", ""),
    ]
    for context_id, goal, domain, sub_topic in demo_queries:
        decisions = engine.apply_rules(context_id, domain=domain or None, coaching_goal=goal, target_sub_topic=sub_topic)
        applied_count = sum(1 for d in decisions if d["applied"])
        print(f"   {context_id}: {applied_count}/{len(decisions)} rules applied for '{goal}'")

    print("\n6. Sample Decisions for ATHLETE-001:")
    cursor = engine.conn.execute("""
        SELECT rule_id, decision, applied
        FROM rule_application_log
        WHERE context_id = 'ATHLETE-001'
        LIMIT 5
    """)
    for rule_id, decision, applied in cursor:
        status = "[APPLIED]" if applied else "[SKIPPED]"
        print(f"   {status} {decision}")

    print("\n[OK] Rule engine ready for deployment")


if __name__ == "__main__":
    main()
