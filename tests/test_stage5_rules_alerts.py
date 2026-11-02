"""Stage 5 gate: planted patterns, rules engine, alerts, cases and analyst actions."""

import collections
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import pytest

from generator import cases, rules
from generator.config import AS_OF, parse_config
from generator.patterns import BENIGN, SUSPICIOUS, plant_counts
from generator.pipeline import EDGE_TRANSACTIONS_PER_CASE, build_clean
from generator.rng import make_rng
from generator.timeutil import DAY, to_seconds
from helpers import assert_ledger_valid, assert_table_integrity

REVIEW_TABLES = ["risk_rules", "alerts", "alert_transactions", "cases", "case_alerts", "analyst_actions",
                 "transactions", "transfer_links", "account_devices", "devices", "account_status_history"]


def _alert_of(t, plant):
    a = t["alerts"]
    hit = a[(a["rule_id"] == plant.rule_id) & (a["account_id"] == plant.account_id)]
    return hit.iloc[0] if len(hit) else None


def test_counts_and_integrity(clean):
    cfg, ds = clean
    t = ds.tables
    assert len(t["alerts"]) == cfg.alerts
    assert len(t["transactions"]) == cfg.transactions - EDGE_TRANSACTIONS_PER_CASE * cfg.edge_cases
    assert_table_integrity(t, REVIEW_TABLES)


def test_ledger_still_valid_after_planting(clean):
    _, ds = clean
    assert_ledger_valid(ds.tables)


def test_every_alert_has_rule_score_reason_and_triggers(clean):
    _, ds = clean
    t = ds.tables
    alerts, bridge = t["alerts"], t["alert_transactions"]
    active = t["risk_rules"].set_index("rule_id")
    assert alerts["rule_id"].map(active["is_active"]).eq(1).all()
    assert alerts["score"].between(0, 100).all()
    assert (alerts["reason"].str.len() > 40).all()
    assert all(r.startswith(code + ":") and "Score =" in r
               for r, code in zip(alerts["reason"], alerts["rule_id"].map(active["rule_code"])))
    counts = bridge.groupby("alert_id").size()
    assert (alerts["alert_id"].map(counts) >= 1).all(), "alert without triggering transaction"
    assert (alerts["alert_id"].map(counts) == alerts["trigger_txn_count"]).all()
    primaries = bridge[bridge["trigger_role"] == "PRIMARY"].groupby("alert_id").size()
    assert (alerts["alert_id"].map(primaries) == 1).all()
    txn = t["transactions"].set_index("transaction_id")
    joined = bridge.join(txn[["amount", "txn_timestamp"]], on="transaction_id")
    assert (alerts["alert_id"].map(joined.groupby("alert_id")["amount"].sum()) == alerts["trigger_amount"]).all()
    assert (alerts["alert_id"].map(joined.groupby("alert_id")["txn_timestamp"].max()) < alerts["created_at"]).all()
    assert (alerts["metric_value"] >= alerts["threshold_value"]).all()
    assert (alerts["threshold_value"] == alerts["rule_id"].map(active["threshold_value"])).all()


def test_scores_follow_the_documented_formula(clean):
    """Recompute every score independently in exact decimal arithmetic (half-up to 2 decimals)."""
    _, ds = clean
    t = ds.tables
    r = t["risk_rules"].set_index("rule_id")
    for row in t["alerts"].itertuples(index=False):
        th, mid, w, cap = (Decimal(str(r.at[row.rule_id, c]))
                           for c in ("threshold_value", "midpoint_value", "weight", "score_ceiling"))
        steps = max(Decimal(0), (Decimal(str(row.metric_value)) - th) / (mid - th))
        expected = (w + (cap - w) * (1 - Decimal("0.5") ** steps)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert Decimal(str(row.score)) == expected, (row.alert_id, row.score, expected)


def test_scores_stay_between_weight_and_ceiling(clean):
    """No score saturates: each lies in [weight, ceiling) of its rule, and the profile rule's ceiling is lower."""
    _, ds = clean
    r = ds.tables["risk_rules"].set_index("rule_id")
    a = ds.tables["alerts"]
    assert (a["score"] >= a["rule_id"].map(r["weight"])).all()
    assert (a["score"] < a["rule_id"].map(r["score_ceiling"])).all()
    pattern_rules = ["RULE-001", "RULE-002", "RULE-003", "RULE-004"]
    assert (r.loc[pattern_rules, "score_ceiling"] == 100).all() and r.at["RULE-005", "score_ceiling"] < 100


def test_ranking_is_total_deterministic_and_explainable(clean):
    _, ds = clean
    a = ds.tables["alerts"]
    assert sorted(a["priority_rank"]) == list(range(1, len(a) + 1))
    ordered = a.sort_values(["score", "created_at", "alert_id"], ascending=[False, True, True])
    assert ordered["priority_rank"].tolist() == list(range(1, len(a) + 1))
    assert a["alert_id"].tolist() == sorted(a["alert_id"])
    assert a["created_at"].is_monotonic_increasing


def test_top_n_selection_keeps_the_best_candidates(clean):
    cfg, ds = clean
    candidates = rules.find_candidates(ds.tables)
    assert len(candidates) > cfg.alerts, "selection must cut candidates for the top-N test to be meaningful"
    kept = set(zip(ds.tables["alerts"]["rule_id"], ds.tables["alerts"]["account_id"], ds.tables["alerts"]["created_at"]))
    dropped = [c for c in candidates if (c.rule_id, c.account_id, c.created_at) not in kept]
    assert len(dropped) == len(candidates) - cfg.alerts
    assert max(c.score for c in dropped) <= ds.tables["alerts"]["score"].min()
    print(f"\n[{cfg.profile}] candidates={len(candidates)} kept={cfg.alerts} "
          f"cut-off score={ds.tables['alerts']['score'].min():.2f} best dropped={max(c.score for c in dropped):.2f}")


def test_each_planted_pattern_is_detected_by_its_rule(clean):
    cfg, ds = clean
    t = ds.tables
    bridge = t["alert_transactions"]
    counts = plant_counts(cfg)
    suspicious = [p for p in ds.plants if p.label == SUSPICIOUS]
    assert len(suspicious) == 5 * counts["suspicious_per_pattern"]
    for plant in suspicious:
        alert = _alert_of(t, plant)
        assert alert is not None, f"{plant.pattern} on {plant.account_id} not detected by {plant.rule_id}"
        triggers = set(bridge.loc[bridge["alert_id"] == alert["alert_id"], "transaction_id"])
        assert {ds.key_to_txn[k] for k in plant.trigger_keys} <= triggers, plant


def test_false_positives_are_present(clean):
    cfg, ds = clean
    t = ds.tables
    benign = [p for p in ds.plants if p.label == BENIGN]
    detected = collections.Counter(p.pattern for p in benign if _alert_of(t, p) is not None)
    for pattern in ("VELOCITY_SPIKE", "NEW_DEVICE_ACTIVITY", "CIRCULAR_TRANSFER", "DORMANT_REACTIVATION"):
        assert detected[pattern] == plant_counts(cfg)["lookalike_per_rule"], pattern
    assert detected["HIGH_AMOUNT_VS_PROFILE"] > 0
    dispositions = t["analyst_actions"].dropna(subset=["disposition"])
    assert (dispositions["disposition"] == "FALSE_POSITIVE").sum() > 0


def test_decision_rule_separates_planted_truth(clean):
    _, ds = clean
    t = ds.tables
    risk = t["kyc_profiles"].set_index("customer_id")["risk_rating"]
    for plant in ds.plants:
        alert = _alert_of(t, plant)
        if alert is None or alert["alert_status"] in ("OPEN", "IN_REVIEW"):
            continue
        if plant.label == SUSPICIOUS:
            assert alert["alert_status"] == "CONFIRMED", plant
        elif plant.pattern != "HIGH_AMOUNT_VS_PROFILE" and risk[alert["customer_id"]] != "HIGH":
            assert alert["alert_status"] == "FALSE_POSITIVE", plant


def test_dispositions_come_from_the_decision_rule(clean):
    _, ds = clean
    t = ds.tables
    context = cases._DecisionContext(t)
    alerts = t["alerts"].set_index("alert_id", drop=False)
    dispo = t["analyst_actions"][t["analyst_actions"]["action_type"] == "DISPOSITION"]
    step = {"FALSE_POSITIVE": "DR-1", "CONFIRMED": "DR-2", "ESCALATED": "DR-3"}
    for row in dispo.itertuples(index=False):
        expected = cases.decide(next(alerts.loc[[row.alert_id]].itertuples(index=False)), context)
        assert (row.disposition, row.decision_basis) == expected
        assert row.decision_basis.startswith(step[row.disposition])


def test_inactive_rule_produces_no_alerts(clean):
    _, ds = clean
    r = ds.tables["risk_rules"]
    retired = r[r["is_active"] == 0]["rule_id"]
    assert list(retired) == ["RULE-006"]
    assert not ds.tables["alerts"]["rule_id"].isin(retired).any()
    assert r.loc[r["is_active"] == 0, "effective_to"].notna().all()


def test_cases_group_related_alerts(clean):
    _, ds = clean
    t = ds.tables
    links = t["case_alerts"].merge(t["alerts"], on="alert_id").merge(t["cases"], on="case_id", suffixes=("", "_case"))
    assert links["alert_id"].is_unique and set(links["alert_id"]) == set(t["alerts"]["alert_id"])
    assert (links["customer_id"] == links["customer_id_case"]).all()
    assert (links["created_at"] - links["opened_at"]).between(0, 14 * DAY).all()
    assert (links["added_at"] == links["created_at"]).all()
    per_case = links.groupby("case_id").agg(n=("alert_id", "size"), top=("score", "max"), first=("created_at", "min"))
    c = t["cases"].set_index("case_id")
    assert (c["alert_count"] == per_case["n"]).all() and (c["max_score"] == per_case["top"]).all()
    assert (c["opened_at"] == per_case["first"]).all()
    best = links.sort_values("priority_rank").groupby("case_id").first()["account_id"]
    assert (c["primary_account_id"] == best).all()
    assert c.index.tolist() == sorted(c.index) and c["opened_at"].is_monotonic_increasing


def test_case_and_disposition_dates_are_ordered(clean):
    _, ds = clean
    t = ds.tables
    acts = t["analyst_actions"].merge(t["cases"][["case_id", "opened_at"]], on="case_id")
    acts = acts.merge(t["alerts"][["alert_id", "created_at"]], on="alert_id", how="left")
    assert (acts["action_timestamp"] <= to_seconds(AS_OF)).all()
    for case, group in acts.sort_values(["case_id", "action_seq"]).groupby("case_id"):
        assert group["action_seq"].tolist() == list(range(1, len(group) + 1))
        assert group["action_type"].iloc[0] == "ASSIGN" and group["action_timestamp"].iloc[0] >= group["opened_at"].iloc[0]
        assert group["action_timestamp"].is_monotonic_increasing, case
        later = group.iloc[1:]
        assert (later["action_timestamp"] >= later["created_at"]).all(), case
        reviewed = set()
        for row in later.itertuples(index=False):
            if row.action_type == "REVIEW":
                reviewed.add(row.alert_id)
            else:
                assert row.alert_id in reviewed, f"{case}: disposition before review"
    dispo = acts[acts["action_type"] == "DISPOSITION"]
    assert (dispo["actor_role"] == dispo["disposition"].map(
        lambda d: "RISK_REVIEWER" if d == "CONFIRMED" else "OPERATIONS_ANALYST")).all()


def test_review_statuses_are_derived(clean):
    _, ds = clean
    t = ds.tables
    acts = t["analyst_actions"]
    dispo = acts[acts["action_type"] == "DISPOSITION"].set_index("alert_id")["disposition"]
    reviewed = set(acts.loc[acts["action_type"] == "REVIEW", "alert_id"])
    expected = [dispo.get(a, "IN_REVIEW" if a in reviewed else "OPEN") for a in t["alerts"]["alert_id"]]
    assert t["alerts"]["alert_status"].tolist() == expected
    status = t["case_alerts"].merge(t["alerts"][["alert_id", "alert_status"]], on="alert_id")
    for case, group in status.groupby("case_id"):
        s = set(group["alert_status"])
        want = "OPEN" if s & {"OPEN", "IN_REVIEW"} else ("ESCALATED" if "ESCALATED" in s else "CLOSED")
        assert t["cases"].set_index("case_id").at[case, "case_status"] == want


def test_clean_build_is_deterministic():
    cfg = parse_config(["--profile", "starter"])
    a = build_clean(cfg, make_rng(cfg.seed)).tables
    b = build_clean(cfg, make_rng(cfg.seed)).tables
    for name in ("alerts", "alert_transactions", "cases", "analyst_actions", "transactions"):
        pd.testing.assert_frame_equal(a[name], b[name])


def test_print_alert_summary(clean):
    cfg, ds = clean
    t = ds.tables
    a = t["alerts"]
    print(f"\n[{cfg.profile}] alerts by rule: {a['rule_id'].value_counts().sort_index().to_dict()}")
    print(f"[{cfg.profile}] score min/median/max: {a['score'].min():.2f} / {a['score'].median():.2f} / {a['score'].max():.2f}")
    print(f"[{cfg.profile}] alert_status: {a['alert_status'].value_counts().to_dict()}")
    print(f"[{cfg.profile}] case_status: {t['cases']['case_status'].value_counts().to_dict()} (cases={len(t['cases'])})")
    found = collections.Counter()
    total = collections.Counter()
    for p in ds.plants:
        total[(p.pattern, p.label)] += 1
        found[(p.pattern, p.label)] += _alert_of(t, p) is not None
    for key in sorted(total):
        print(f"[{cfg.profile}]   planted {key[0]:<23} {key[1]:<10} detected {found[key]}/{total[key]}")
    top = a.sort_values("priority_rank").head(3)
    for row in top.itertuples(index=False):
        print(f"[{cfg.profile}]   #{row.priority_rank} {row.alert_id} {row.rule_id} score={row.score:.2f} :: {row.reason[:110]}")


@pytest.mark.parametrize("pattern", ["VELOCITY_SPIKE", "NEW_DEVICE_ACTIVITY", "CIRCULAR_TRANSFER",
                                     "DORMANT_REACTIVATION", "HIGH_AMOUNT_VS_PROFILE"])
def test_every_pattern_type_is_planted(clean, pattern):
    _, ds = clean
    labels = {p.label for p in ds.plants if p.pattern == pattern}
    assert labels == {SUSPICIOUS, BENIGN}
