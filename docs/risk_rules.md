# Risk rules, ranking and disposition decision rule

All rules are rows of the `risk_rules` table (owned by the system administrator, approved by
a risk reviewer) and are evaluated only over source records. Implementation:
[`generator/rules.py`](../generator/rules.py) and [`generator/cases.py`](../generator/cases.py).
Neither module reads the ground truth (enforced by `tests/test_stage6_edge_cases.py`).

## Scoring (same formula for every rule)

```
score = weight + (ceiling - weight) * (1 - 0.5 ^ ((metric - threshold) / (midpoint - threshold)))
```

evaluated in exact decimal arithmetic on the stored `metric_value` (4 decimals) and rounded
half-up to 2 decimals, so anyone can recompute it exactly. A rule fires only when
`metric >= threshold`. Reading the formula:

* a hit that just meets the threshold scores the rule's `weight`;
* at `midpoint` the score is halfway between `weight` and the rule's `score_ceiling`;
* every further `(midpoint - threshold)` of metric halves the remaining distance to the ceiling,
  so a larger metric always scores higher and the score **never saturates** (no plateau). After
  rounding to 2 decimals, a score would show its ceiling only once the remaining distance is
  below 0.005 points (for RULE-005, a ratio of about 41.6× or more). No alert in the generated
  datasets gets that far: a test and `scripts/acceptance_check.py` both verify it.

**Why the ceilings differ.** The four pattern rules (RULE-001..004) need several related events
and have ceiling 100. RULE-005 looks at one payment against a figure the customer declared at
onboarding. An extreme ratio more often means a stale profile than a criminal pattern, so its
ceiling is 92: however large the ratio, one outlier payment ranks below any pattern alert that
scores above 92. Its midpoint, 6× (twice the threshold), keeps the smallest planted suspicious
ratio (8×) at 80.35 or more, above the DR-2 confirmation bar of 80.

The alert row stores `metric_value`, `threshold_value` and `score`; its `reason` restates the
facts and the arithmetic, e.g.

> VELOCITY_SPIKE: 12 debits within 24h from 2026-01-28T19:19:27 at 4 distinct merchant(s), total EUR 4826.71. Threshold 6. Score = 60 + (100 - 60) x (1 - 0.5^((12 - 6) / (9 - 6))) = 90.00

> HIGH_AMOUNT_VS_PROFILE: single PURCHASE of EUR 7540.46 is 13.96x the customer's KYC expected maximum transaction of EUR 540.00. Threshold 3.00x. Score = 55 + (92 - 55) x (1 - 0.5^((13.9638 - 3) / (6 - 3))) = 89.06

## Rules

| rule_id | code | metric (unit) | threshold | midpoint | weight | ceiling | what fires it |
|---|---|---|---|---|---|---|---|
| RULE-001 | VELOCITY_SPIKE | debits in a 24h window (COUNT) | 6 | 9 | 60 | 100 | ≥ 6 customer-initiated debits (PURCHASE, ATM_WITHDRAWAL, BILL_PAYMENT, TRANSFER_OUT) within 24 h of the first debit of a burst; one alert per burst |
| RULE-002 | NEW_DEVICE_ACTIVITY | EUR debited via a new device within 48h (EUR) | 1,000 | 3,000 | 65 | 100 | debits through a device whose account link (`first_seen`) is ≥ 7 days after account opening, within 48 h of `first_seen`; one alert per (account, device) |
| RULE-003 | CIRCULAR_TRANSFER | first-hop amount (EUR) | 500 | 5,250 | 75 | 100 | time-ordered chain of transfer links that returns to the origin through ≥ 3 distinct accounts within 72 h, every hop within ±10 % of the first hop; anchored at the origin account |
| RULE-004 | DORMANT_REACTIVATION | EUR debited within 7 days of reactivation (EUR) | 2,000 | 11,000 | 70 | 100 | customer-initiated debits in the 7 days after a DORMANT→ACTIVE status event |
| RULE-005 | HIGH_AMOUNT_VS_PROFILE | amount / KYC `expected_max_txn_amount` (RATIO) | 3.0 | 6.0 | 55 | 92 | a single PURCHASE, ATM_WITHDRAWAL or BILL_PAYMENT ≥ 3× the declared maximum |
| RULE-006 | ROUND_AMOUNT_STRUCTURING | — | — | — | — | — | retired 2025-12-31 (`is_active = 0`); kept for traceability, never evaluated |

Alert facts:

* `created_at` = the first full hour after the last triggering transaction (hourly detection batch).
* `alert_transactions` holds every triggering transaction; the one that completed the
  condition is `PRIMARY` (e.g. the 6th debit of a burst, the debit that crossed the amount
  threshold, the credit leg that closed a ring).
* `trigger_txn_count` and `trigger_amount` summarise the bridge rows.

## Selection and ranking

1. All hits of active, effective rules are candidates.
2. Candidates are ordered by `score` DESC, `created_at` ASC, `account_id`, `rule_id`, primary
   transaction id; the first `--alerts` are kept (the generator fails loudly if there are fewer).
3. Alert ids are assigned by `created_at` (then account, rule, primary transaction).
4. `priority_rank` = position by `score` DESC, `created_at` ASC, `alert_id` ASC — a total,
   deterministic order that an analyst can reproduce from the table alone.

Because the score never saturates, the order is driven by the score itself; the tie-break
(older alert first, because it has waited longest, then the lower alert id) only separates
alerts with exactly the same score, typically velocity bursts with the same debit count. Q1 in
`sample_queries.sql` prints `score` and `created_at` side by side. At full scale the top 10 of
the queue are all planted suspicious patterns (RULE-002 new-device activity); the RULE-005
ceiling keeps benign big-ticket outliers below them.

## Cases and analyst actions

* Alerts of the same customer created within 14 days of a case's first alert share that case.
* `ASSIGN` (SYSTEM → an `OPS-nn` analyst) 5 min – 4 h after the case opens; then per alert in
  creation order a `REVIEW` (30 min – 72 h later, or 3 – 30 days for the 30 % backlog) and a
  `DISPOSITION` (10 min – 48 h after the review).
* Actions that would fall after the snapshot (2026-10-22T00:00:00) do not exist yet; such
  alerts are `OPEN` (not reviewed) or `IN_REVIEW` (reviewed, no disposition).

## Disposition decision rule

Applied to each alert using its triggering transactions and related source records
(merchants, transfer links, device links, status history, KYC):

| step | outcome | condition |
|---|---|---|
| DR-1 | FALSE_POSITIVE | the rule's mitigating condition holds **and** KYC `risk_rating` ≠ HIGH |
| DR-2 | CONFIRMED | the rule's aggravating condition holds **and** `score` ≥ 80 |
| DR-3 | ESCALATED | anything else (mitigating but HIGH-risk customer, aggravating but score < 80, or no context) |

| rule | mitigating condition | aggravating condition |
|---|---|---|
| RULE-001 | all triggering debits at one merchant in TRANSPORT / FUEL / GROCERY | debits at ≥ 3 distinct merchants |
| RULE-002 | every payee (merchant or destination account) was already paid before the device's `first_seen` | at least one payee never paid before |
| RULE-003 | every account in the ring belongs to the same customer | the ring spans ≥ 2 customers |
| RULE-004 | latest reactivation reason is REACTIVATED_IN_BRANCH | latest reactivation reason is REACTIVATED_ONLINE |
| RULE-005 | merchant category is a planned purchase (TRAVEL, FURNITURE, HOME_IMPROVEMENT, AUTOMOTIVE, ELECTRONICS, INSURANCE) | merchant category is elevated risk (GIFT_CARDS, CRYPTO_EXCHANGE, MONEY_SERVICES, GAMBLING) |

`CONFIRMED` is recorded by a `RISK_REVIEWER` (four-eyes); `FALSE_POSITIVE` and `ESCALATED` by
the assigned `OPERATIONS_ANALYST`. `decision_basis` names the step and the facts it used, e.g.
`DR-1: all debits at one TRANSPORT merchant MER-000017; KYC risk LOW`.

Because the decision rule only sees observable data, it is deliberately imperfect: benign
behaviour of HIGH-risk customers is escalated, and big-ticket purchases in neutral categories
(GENERAL_RETAIL, JEWELRY) are escalated. `validation_summary` measures this against the ground truth.
