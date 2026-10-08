"""Versioned rule data. Each fact records its source and a verification status."""

import json
from pathlib import Path

CFA_RULES_PATH = Path(__file__).parent / "cfa_rules.json"


def load_cfa_rules(path=CFA_RULES_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pending_facts(rules):
    """Ids of facts and sources whose verification is still pending."""
    out = []
    for s in rules.get("sources", []):
        if s.get("verification") != "verified":
            out.append(s["source_id"])
    for key in ("effective_from", "special_category"):
        fact = rules.get(key) or {}
        if fact.get("verification") != "verified":
            out.append(fact.get("fact_id", key))
    for r in rules.get("rules", []):
        if r.get("verification") != "verified":
            out.append(r["rule_id"])
    return out
