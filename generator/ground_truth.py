"""Stage 6: ground truth.

Labels every planted scenario and every injected edge case. The ground truth is an
evaluation artefact written to its own files; it is not a table of the schema, none of
its fields is a column of an input table, and no rule, feature or disposition module
imports this module (tests enforce all three).
"""

from __future__ import annotations

import pandas as pd

from .edge_cases import EdgeCase
from .patterns import SUSPICIOUS, Plant

COLUMNS = ["ground_truth_id", "category", "entity_type", "entity_id", "issue_or_pattern_type",
           "expected_label", "expected_rule_id", "notes"]


def build(plants: list[Plant], key_to_txn: dict[int, str], edge_cases: list[EdgeCase]) -> pd.DataFrame:
    """One row per planted scenario (anchor account) and per injected edge case."""
    rows = []
    for plant in plants:
        triggers = ", ".join(key_to_txn[k] for k in plant.trigger_keys)
        members = f"; members: {', '.join(plant.members)}" if len(plant.members) > 1 else ""
        rows.append({
            "category": "PLANTED_PATTERN" if plant.label == SUSPICIOUS else "FALSE_POSITIVE_PLANT",
            "entity_type": "ACCOUNT", "entity_id": plant.account_id, "issue_or_pattern_type": plant.pattern,
            "expected_label": plant.label, "expected_rule_id": plant.rule_id,
            "notes": f"{plant.notes}{members}; trigger transactions: {triggers}",
        })
    for case in edge_cases:
        rows.append({
            "category": "EDGE_CASE", "entity_type": case.entity_type, "entity_id": case.entity_id,
            "issue_or_pattern_type": case.issue_type, "expected_label": "INVALID", "expected_rule_id": None,
            "notes": case.notes,
        })
    frame = pd.DataFrame(rows, columns=COLUMNS[1:])
    frame.insert(0, "ground_truth_id", [f"GT-{i:06d}" for i in range(1, len(frame) + 1)])
    return frame
