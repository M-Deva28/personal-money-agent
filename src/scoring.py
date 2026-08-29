"""
Shared scoring logic — used by both score.py (CLI) and main.py (API)
so there's exactly one source of truth for how precision/recall/F1
and the rupee breakdown are computed.
"""

from collections import defaultdict


def compute_score(flags, ground_truth):
    """
    flags: list of flag dicts from detector.run_all_detectors()
    ground_truth: list of {"id": ..., "label": ...} from data/ground_truth.json

    Returns a JSON-serializable dict with the full report.
    """
    truth_by_id = {g["id"]: g["label"] for g in ground_truth}
    flagged_ids = {f["event_id"]: f["pattern"] for f in flags}
    all_labeled_ids = set(truth_by_id.keys())

    true_positives = sum(
        1 for eid, pat in flagged_ids.items() if truth_by_id.get(eid) == pat
    )
    false_positives = sum(
        1 for eid, pat in flagged_ids.items() if truth_by_id.get(eid, "clean") != pat
    )
    false_negatives = sum(
        1 for eid, label in truth_by_id.items()
        if label != "clean" and flagged_ids.get(eid) != label
    )

    precision = true_positives / (true_positives + false_positives) if flagged_ids else 0
    recall = true_positives / (true_positives + false_negatives) if all_labeled_ids else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    amount_flagged = sum(f["amount_at_stake"] for f in flags)
    high_conf_amount = sum(f["amount_at_stake"] for f in flags if f["confidence"] == "high")

    by_pattern = defaultdict(int)
    for f in flags:
        by_pattern[f["pattern"]] += 1

    return {
        "total_flags": len(flags),
        "flags_by_pattern": dict(by_pattern),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "total_amount_flagged": round(amount_flagged, 2),
        "auto_actionable_amount": round(high_conf_amount, 2),
        "needs_review_amount": round(amount_flagged - high_conf_amount, 2),
    }
