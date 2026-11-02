"""Stage 5c: case grouping, the disposition decision rule and analyst actions.

Cases group the alerts of one customer whose ``created_at`` lies within 14 days of the
case's first alert. Each case is assigned to an operations analyst, then every alert
is reviewed and dispositioned in order. Dispositions are not random: they come from a
documented decision rule applied to the alert and its source records:

* DR-1 FALSE_POSITIVE - the rule's mitigating condition holds and KYC risk is not HIGH
* DR-2 CONFIRMED      - the rule's aggravating condition holds and score >= 80
* DR-3 ESCALATED      - anything else (sent to risk / compliance)

Mitigating / aggravating conditions per rule:

========  ===============================================  =======================================
rule      mitigating                                       aggravating
========  ===============================================  =======================================
RULE-001  all debits at one TRANSPORT/FUEL/GROCERY merchant  debits at >= 3 distinct merchants
RULE-002  every payee was already paid before the device   at least one payee never paid before
RULE-003  every account in the ring has the same owner     ring spans >= 2 customers
RULE-004  reactivated in branch (REACTIVATED_IN_BRANCH)    reactivated online (REACTIVATED_ONLINE)
RULE-005  merchant in a planned-purchase category          merchant in an elevated-risk category
========  ===============================================  =======================================

Only the action timing (lags) is random: reviews normally follow within 30 min - 72 h,
30% wait 3-30 days in the backlog. Actions that would fall after the snapshot
(``AS_OF``) have not happened yet, so those alerts stay OPEN / IN_REVIEW.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import ids
from .master_data import AS_OF_S, ELEVATED_CATEGORIES, LOW_RISK_REPEAT_CATEGORIES, PLANNED_PURCHASE_CATEGORIES
from .tables import make_frame
from .timeutil import DAY, HOUR, MINUTE

CASE_WINDOW = 14 * DAY
CONFIRM_MIN_SCORE = 80.0
BACKLOG_SHARE = 0.30  # reviews that wait 3-30 days in the queue instead of 30 min - 72 h
OPERATIONS_ANALYSTS = [f"OPS-{i:02d}" for i in range(1, 9)]
RISK_REVIEWERS = [f"RISK-{i:02d}" for i in range(1, 4)]


def build(tables: dict[str, pd.DataFrame], rng: np.random.Generator) -> None:
    """Create cases, case_alerts and analyst_actions; derive alert and case statuses."""
    alerts = tables["alerts"]
    groups = _group(alerts)
    groups.sort(key=lambda g: (int(alerts.loc[g[0], "created_at"]), alerts.loc[g[0], "customer_id"]))
    context = _DecisionContext(tables)
    case_rows, link_rows, actions = [], [], []
    for n, members in enumerate(groups, start=1):
        case = ids.case_id(n)
        rows = alerts.loc[members]
        best = rows.sort_values("priority_rank").iloc[0]
        opened = int(rows["created_at"].min())
        case_rows.append((case, best["customer_id"], best["account_id"], opened, len(rows), float(rows["score"].max())))
        link_rows.extend((case, a.alert_id, int(a.created_at)) for a in rows.itertuples(index=False))
        actions.extend(_work_case(case, opened, rows, context, rng))

    tables["cases"] = make_frame("cases", {
        "case_id": [r[0] for r in case_rows], "customer_id": [r[1] for r in case_rows],
        "primary_account_id": [r[2] for r in case_rows],
        "opened_at": np.array([r[3] for r in case_rows], dtype=np.int64),
        "alert_count": [r[4] for r in case_rows], "max_score": [r[5] for r in case_rows],
        "case_status": [None] * len(case_rows),
    })
    tables["case_alerts"] = make_frame("case_alerts", {
        "case_id": [r[0] for r in link_rows], "alert_id": [r[1] for r in link_rows],
        "added_at": np.array([r[2] for r in link_rows], dtype=np.int64),
    })
    actions.sort(key=lambda a: (a["action_timestamp"], a["case_id"], a["action_seq"]))
    for i, action in enumerate(actions, start=1):
        action["action_id"] = ids.action_id(i)
    columns = ["action_id", "case_id", "alert_id", "action_seq", "action_type", "actor_role", "actor_id",
               "assigned_to", "action_timestamp", "disposition", "decision_basis"]
    data = {c: [a[c] for a in actions] for c in columns}
    data["action_timestamp"] = np.array(data["action_timestamp"], dtype=np.int64)
    tables["analyst_actions"] = make_frame("analyst_actions", data)
    derive_review_statuses(tables)


def _group(alerts: pd.DataFrame) -> list[list[int]]:
    """Alert row indices per case: same customer, created within 14 days of the case's first alert."""
    groups: list[list[int]] = []
    ordered = alerts.sort_values(["customer_id", "created_at", "alert_id"], kind="mergesort")
    current_customer, opened = None, 0
    for idx, row in zip(ordered.index, ordered.itertuples(index=False)):
        if row.customer_id != current_customer or row.created_at - opened > CASE_WINDOW:
            groups.append([])
            current_customer, opened = row.customer_id, row.created_at
        groups[-1].append(idx)
    return groups


def _work_case(case: str, opened: int, rows: pd.DataFrame, context: "_DecisionContext",
               rng: np.random.Generator) -> list[dict]:
    """ASSIGN, then REVIEW and DISPOSITION per alert, stopping at the snapshot date."""
    analyst = OPERATIONS_ANALYSTS[int(rng.integers(len(OPERATIONS_ANALYSTS)))]
    ts = opened + int(rng.integers(5 * MINUTE, 4 * HOUR))
    out = [_action(case, None, 1, "ASSIGN", "SYSTEM", "SYSTEM", ts, assigned_to=analyst)]
    for alert in rows.sort_values(["created_at", "alert_id"]).itertuples(index=False):
        backlog = rng.random() < BACKLOG_SHARE
        review_lag = rng.integers(3 * DAY, 30 * DAY) if backlog else rng.integers(30 * MINUTE, 72 * HOUR)
        review_ts = max(ts, int(alert.created_at)) + int(review_lag)
        disposition_ts = review_ts + int(rng.integers(10 * MINUTE, 48 * HOUR))
        reviewer = RISK_REVIEWERS[int(rng.integers(len(RISK_REVIEWERS)))]
        if review_ts > AS_OF_S:
            break
        out.append(_action(case, alert.alert_id, len(out) + 1, "REVIEW", "OPERATIONS_ANALYST", analyst, review_ts))
        ts = review_ts
        if disposition_ts > AS_OF_S:
            break
        disposition, basis = decide(alert, context)
        role, actor = ("RISK_REVIEWER", reviewer) if disposition == "CONFIRMED" else ("OPERATIONS_ANALYST", analyst)
        out.append(_action(case, alert.alert_id, len(out) + 1, "DISPOSITION", role, actor, disposition_ts,
                           disposition=disposition, decision_basis=basis))
        ts = disposition_ts
    return out


def _action(case: str, alert: str | None, seq: int, action_type: str, role: str, actor: str, ts: int,
            assigned_to: str | None = None, disposition: str | None = None, decision_basis: str | None = None) -> dict:
    return {"case_id": case, "alert_id": alert, "action_seq": seq, "action_type": action_type, "actor_role": role,
            "actor_id": actor, "assigned_to": assigned_to, "action_timestamp": int(ts), "disposition": disposition,
            "decision_basis": decision_basis}


class _DecisionContext:
    """Source-record lookups used by the decision rule (built once per dataset)."""

    def __init__(self, tables: dict[str, pd.DataFrame]):
        txns = tables["transactions"]
        self.txns = txns.set_index("transaction_id")
        self.category = tables["merchants"].set_index("merchant_id")["category"]
        self.kyc_risk = tables["kyc_profiles"].set_index("customer_id")["risk_rating"]
        bridge = tables["alert_transactions"]
        self.triggers = {a: g["transaction_id"].tolist() for a, g in bridge.groupby("alert_id", sort=False)}
        self.first_seen = {(r.account_id, r.device_id): int(r.first_seen)
                           for r in tables["account_devices"].itertuples(index=False)}
        links = tables["transfer_links"]
        payee = txns["merchant_id"].copy()
        transfer_dest = txns["transaction_id"].map(links.set_index("source_txn_id")["dest_account_id"])
        payee = payee.fillna(transfer_dest)
        paid = txns.assign(payee=payee)[(txns["direction"] == "DEBIT") & payee.notna()]
        self.payments = {a: g[["txn_timestamp", "payee"]] for a, g in paid.groupby("account_id", sort=False)}
        self.payee = payee.set_axis(txns["transaction_id"])
        hist = tables["account_status_history"]
        self.reactivations = hist[(hist["from_status"] == "DORMANT") & (hist["to_status"] == "ACTIVE")]

    def conditions(self, alert) -> tuple[str | None, str | None]:
        """(mitigating fact, aggravating fact) for an alert; None where the condition does not hold."""
        triggers = self.txns.loc[self.triggers[alert.alert_id]]
        if alert.rule_id == "RULE-001":
            merchants = sorted(triggers["merchant_id"].dropna().unique())
            categories = sorted(self.category.loc[merchants].unique()) if merchants else []
            if len(merchants) == 1 and categories[0] in LOW_RISK_REPEAT_CATEGORIES:
                return f"all debits at one {categories[0]} merchant {merchants[0]}", None
            if len(merchants) >= 3:
                return None, f"debits at {len(merchants)} distinct merchants"
            return None, None
        if alert.rule_id == "RULE-002":
            first = triggers.iloc[0]
            seen = self.first_seen[(alert.account_id, first["device_id"])]
            history = self.payments.get(alert.account_id)
            known = set(history[history["txn_timestamp"] < seen]["payee"]) if history is not None else set()
            payees = sorted(self.payee.loc[triggers.index].unique())
            new = [p for p in payees if p not in known]
            if new:
                return None, f"payee(s) never paid before the new device: {', '.join(new)}"
            return f"every payee ({', '.join(payees)}) was paid before the device was first used", None
        if alert.rule_id == "RULE-003":
            owners = sorted(triggers["account_id"].str[4:14].unique())
            if len(owners) == 1:
                return f"all ring accounts belong to {owners[0]} (own-account sweep)", None
            return None, f"ring spans {len(owners)} customers"
        if alert.rule_id == "RULE-004":
            events = self.reactivations[(self.reactivations["account_id"] == alert.account_id)
                                        & (self.reactivations["event_timestamp"] <= triggers["txn_timestamp"].min())]
            reason = events.sort_values("event_timestamp")["reason_code"].iloc[-1]
            if reason == "REACTIVATED_IN_BRANCH":
                return "reactivated in branch with identity re-verification", None
            return None, f"reactivated remotely ({reason})"
        merchant = triggers["merchant_id"].iloc[0]
        category = None if pd.isna(merchant) else self.category[merchant]
        if category in PLANNED_PURCHASE_CATEGORIES:
            return f"planned-purchase category {category}", None
        if category in ELEVATED_CATEGORIES:
            return None, f"elevated-risk merchant category {category}"
        return None, None


def decide(alert, context: _DecisionContext) -> tuple[str, str]:
    """Apply decision rule DR-1..DR-3 to one alert; return (disposition, decision_basis)."""
    mitigating, aggravating = context.conditions(alert)
    risk = context.kyc_risk[alert.customer_id]
    if mitigating and risk != "HIGH":
        return "FALSE_POSITIVE", f"DR-1: {mitigating}; KYC risk {risk}"
    if aggravating and alert.score >= CONFIRM_MIN_SCORE:
        return "CONFIRMED", f"DR-2: {aggravating}; score {alert.score:.2f} >= {CONFIRM_MIN_SCORE:.0f}"
    if mitigating:
        return "ESCALATED", f"DR-3: {mitigating}, but KYC risk HIGH"
    if aggravating:
        return "ESCALATED", f"DR-3: {aggravating}, but score {alert.score:.2f} < {CONFIRM_MIN_SCORE:.0f}"
    return "ESCALATED", "DR-3: no mitigating or aggravating context"


def derive_review_statuses(tables: dict[str, pd.DataFrame]) -> None:
    """alert_status from the alert's actions; case_status from its alerts' dispositions."""
    actions = tables["analyst_actions"]
    disposition = actions[actions["action_type"] == "DISPOSITION"].set_index("alert_id")["disposition"]
    reviewed = set(actions.loc[actions["action_type"] == "REVIEW", "alert_id"])
    alerts = tables["alerts"]
    alerts["alert_status"] = [disposition.get(a, "IN_REVIEW" if a in reviewed else "OPEN") for a in alerts["alert_id"]]
    status = tables["case_alerts"].merge(alerts[["alert_id", "alert_status"]], on="alert_id")

    def case_status(statuses: pd.Series) -> str:
        if statuses.isin(["OPEN", "IN_REVIEW"]).any():
            return "OPEN"
        return "ESCALATED" if (statuses == "ESCALATED").any() else "CLOSED"

    per_case = status.groupby("case_id")["alert_status"].agg(case_status)
    tables["cases"]["case_status"] = tables["cases"]["case_id"].map(per_case)
