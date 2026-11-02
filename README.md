# MintMule: synthetic AML analyst-review dataset generator

**MintMule** *mints* synthetic banking data, including the money-*mule* style patterns that AML
analysts hunt for. Each `source_manifest.json` identifies the generator as
`synthetic-aml-review-generator` 1.0.0.

A reproducible generator of **synthetic** data for a fraud / AML-style workflow that prioritises
transactions and linked accounts for analyst review using transparent rules. It produces
master data, a reconciled transaction ledger, planted suspicious patterns and benign
look-alikes, rule-based alerts with explainable scores and rankings, cases, analyst actions
with rule-based dispositions, labelled data-quality edge cases, a separate ground truth, an
independent validation report and SQLite/CSV/JSON exports.

> **All data is synthetic.** No real personal data and no external data sources are used. Names
> are built from a fixed syllable list; every value comes from one seeded random generator. The
> statement is repeated in every `source_manifest.json`.

Actors represented: customer / account holder, operations analyst, risk / compliance reviewer,
system administrator (see [docs/data_dictionary.md](docs/data_dictionary.md#actors)).

> **Key design choices**
>
> * **RULE-005 dominates the alert queue on purpose.** High amount vs. profile produces about 74 % of
>   the alerts and has a ~74 % false-positive rate, against ~30 % for the four pattern rules. Benign
>   big-ticket purchases are planted at ~85 % of `--alerts` so that the top-N cut is really exercised
>   and false positives are plentiful and measurable, as with profile-deviation rules in production.
>   See *Design decisions*.
> * **Edge-case transactions carry the highest ids.** They are appended after the valid data, so
>   `TXN-2026-00099961` .. `TXN-2026-00100000` have earlier timestamps than some lower ids. Ledgers are
>   always read by timestamp, then id.
> * **`risk_rules` has six rows but only five rules fire.** RULE-006 is retired (`is_active = 0`,
>   `effective_to = 2025-12-31`) and is kept for rule-lifecycle traceability.
> * **Scores never saturate, and one outlier payment cannot outrank a strong pattern.** Each rule
>   approaches its own `score_ceiling` without reaching it: 100 for the four pattern rules, 92 for
>   the single-transaction RULE-005. The ranking is therefore decided by the score; the tie-break
>   (older alert first, then `alert_id`) only separates identical scores. At full scale the top 10
>   of the queue are all planted suspicious patterns.
> * **Scores are comparable within a rule, not calibrated across rules.** A higher score always
>   means a stronger hit of the same rule. How rules rank against each other follows the hand-set
>   weights, midpoints and ceilings, which is why the first 14 alerts are all RULE-002 (new-device).
>   See *Design decisions*.

## Folder structure

```
generator/                 one module per stage
  config.py rng.py ids.py timeutil.py   stage 1  CLI, profiles, seeded RNG, ID helpers, calendar
  schema.sql schema.py                  stage 2  SQLite contract (authoritative) + introspection, ER diagram
  master_data.py                        stage 3  customers, KYC, accounts, merchants, devices, links, status history
  transactions.py                       stage 4  baseline events, transfers, ledger posting
  patterns.py rules.py cases.py         stage 5  planted scenarios, rules engine, alerts, cases, actions
  edge_cases.py ground_truth.py         stage 6  labelled edge-case injection, ground truth
  validator.py                          stage 7  independent validator (reads CSV + schema.sql only)
  exports.py load_sqlite.py             stage 8  CSV / JSON / SQL / manifest / checksums / docs; SQLite loader
  reproducibility.py                    stage 9  run-twice / other-seed checksum comparison
  tables.py                             shared   schema-shaped frames and text rendering
  pipeline.py __main__.py               entry point: python -m generator
tests/                     test_stage1_skeleton.py ... test_stage10_docs.py, helpers, fixtures
scripts/acceptance_check.py read-only manual acceptance report for a dataset folder
docs/                      data_dictionary.md, er_diagram.md (Mermaid), risk_rules.md,
                           sample_queries.sql, reproducibility.md, development_history.md
output/                    generated datasets: output/full, output/starter (created by the generator)
README.md  PROMPT_LOG.md  requirements.txt  Makefile  pytest.ini
```

## Requirements

* Python **3.11+** (developed and tested on 3.11.0)
* numpy 2.2.1, pandas 2.2.3, pytest 8.3.4 (pinned in `requirements.txt`); nothing else, no network access at run time

## Install, configure, run and verify

Step by step for a first run. Run every command from the project folder (the folder that
contains this README). Verified on Windows 11 with Python 3.11.0; the Linux / macOS commands
follow the same steps.

### 1. Get the code

```bash
git clone https://github.com/Aaron-hub-dev-maker/MintMule.git
cd MintMule
```

Or unzip the submission and open a terminal in the extracted folder.

### 2. Install dependencies

Windows (PowerShell):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell refuses to run `Activate.ps1`, allow scripts for this window only, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Linux / macOS:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The prompt now starts with `(.venv)`. The install is needed once; in a new terminal only
activate again. All later commands use `python`, which is the virtual environment's Python.

### 3. Configure (optional)

Nothing has to be configured: there is no configuration file and no environment variable. The
defaults produce the full dataset (5,000 customers, 7,000 accounts, 100,000 transactions,
1,000 alerts) with seed 20261022. Everything else is a command-line option, listed under
[Generate](#generate): `--profile starter` for a small dataset, `--seed`, `--customers`,
`--accounts`, `--transactions`, `--alerts`, `--edge-cases` and `--out`.

### 4. Run

```bash
python -m generator --profile starter
python -m generator
```

Each run writes its dataset folder (`output/starter`, `output/full`) and validates it. The last
line must end with `exact_match=True`:

* starter: `validation: findings=20 expected_edge_cases=20 matched=20 exact_match=True`
* full: `validation: findings=50 expected_edge_cases=50 matched=50 exact_match=True`

### 5. Verify key behaviour

Run these in order; the acceptance check reads the results of the steps before it.

```bash
python -m pytest
python -m generator.reproducibility --profile full
python -m generator.load_sqlite output/full
python scripts/acceptance_check.py output/full
```

| command | what it proves | expected result |
|---|---|---|
| `python -m pytest` | every stage gate on both scales: IDs, keys, reconciled balances, planted patterns detected, false positives present, explainable ranking, edge cases, validator, exports, docs | `209 passed` (about 3-4 minutes) |
| `python -m generator.reproducibility --profile full` | same seed gives byte-identical output, another seed does not | `same seed identical: True (41/41 files)` and `different seed: 34/41 files differ` |
| `python -m generator.load_sqlite output/full` | `seed.sql` loads into SQLite; sample queries run | `loaded: customers=5000, ...`, then Q1-Q4: top 10 alerts, false-positive rate per rule, why the top alert ranks first, data-quality exceptions |
| `python scripts/acceptance_check.py output/full` | every acceptance point with its evidence (see [Manual acceptance check](#manual-acceptance-check)) | ten sections, ending `ALL CHECKS PASSED` |

With the pinned package versions on Windows, the same-seed runs give the checksum-file hash
`7d05fc11f8f370c23c63649db8f4c015ac12657bdd3d1b51eaa50ed027c2eb26`, also recorded in
[docs/reproducibility.md](docs/reproducibility.md). If any step fails, the command exits
non-zero and prints the failing test or check.

## Generate

```bash
python -m generator --profile starter          # 200 customers / 280 accounts / 4,000 txns / 40 alerts  -> output/starter
python -m generator                            # full: 5,000 / 7,000 / 100,000 / 1,000                   -> output/full
python -m generator --seed 7 --customers 1000 --accounts 1400 --transactions 20000 --alerts 200 --out output/custom
```

| option | default | meaning |
|---|---|---|
| `--profile` | `full` | `full` or `starter`; supplies the defaults below (same code path) |
| `--seed` | `20261022` | seed of the single `numpy.random.Generator` (PCG64) |
| `--customers` | 5000 / 200 | customers (exact) |
| `--accounts` | 7000 / 280 | accounts (exact, ≥ customers, ≤ 99 × customers) |
| `--transactions` | 100000 / 4000 | transactions in the final dataset (exact, including planted and edge-case rows) |
| `--alerts` | 1000 / 40 | alerts in the final dataset (exact: top-N candidates by score) |
| `--edge-cases` | 10 / 4 | injected rows per edge-case type |
| `--out` | `output/<profile>` | output folder |

A full run takes about 45 s on a laptop, the starter run about 3 s. The command exits non-zero if
the validator's findings are not exactly the labelled edge cases.

## Test, validate, reproduce

```bash
python -m pytest                                   # all stage gates (about 3-4 minutes, 209 tests, both scales)
python -m pytest tests/test_stage5_rules_alerts.py -v -s   # one stage, with printed summaries
python -m generator.validator output/full          # independent validation -> output/full/validation_summary.{json,md}
python -m generator.reproducibility --profile full # 3 runs, checksum comparison -> docs/reproducibility.md
python -m generator.load_sqlite output/full        # build output/full/aml_review.db and run docs/sample_queries.sql
```

In the pytest progress output, `test_stage5` to `test_stage7` appear in more than one block. That is
not a repeat: their tests run on both the starter and the full dataset, and pytest groups them by
dataset. Check [10] of the acceptance script confirms that all 209 test ids are unique.

The seed is fixed at **20261022** unless `--seed` is given. Reproducibility evidence for the full
profile (two identical runs and one different-seed run) is in [docs/reproducibility.md](docs/reproducibility.md).

## Manual acceptance check

After generating `output/full` and running the reproducibility check, one read-only script prints
the evidence and a PASS / FAIL verdict for every acceptance point: full-scale validator findings
versus the labelled edge cases, checksum verification (same seed identical, other seed different),
proof of each of the five edge cases in the data and in `ground_truth.csv`, presence of every
deliverable, exact row counts, the ranking (no score at its rule's ceiling, the tie-break, planted
suspicious alerts at the head of the queue), the rule mix, the retired RULE-006, the appended
edge-case ids, and that no test is collected twice.

```bash
python scripts/acceptance_check.py output/full
```

It ends with `ALL CHECKS PASSED` (exit code 0) or lists the failed checks (exit code 1).

## Output files (per dataset folder)

| file | content |
|---|---|
| `csv/<table>.csv` | 15 tables, UTF-8, LF, header = schema columns, empty field = NULL |
| `json/<table>.json` | same rows as JSON records, typed (INTEGER → number, REAL → number, NULL → null) |
| `sql/schema.sql` | SQLite DDL: PKs, FKs (incl. composite), CHECK constraints, append-only triggers |
| `sql/seed.sql` | INSERTs in FK order inside one transaction |
| `ground_truth.csv` / `.json` | labels for planted patterns, benign look-alikes and edge cases (not an input table) |
| `source_manifest.json` | synthetic-only statement, seed, parameters, generator version, environment, row counts |
| `validation_summary.json` / `.md` | validator findings, reconciliation with ground truth, measurable metrics |
| `checksums.sha256` | SHA-256 of every other file (`sha256sum -c` compatible) |
| `docs/` | data dictionary, ER diagram, rule documentation, sample queries |

Transaction ids follow posting order. The edge-case transactions are appended **after** the valid
data, so they carry the highest ids (`TXN-2026-00099961` .. `TXN-2026-00100000` at full scale) even
though their timestamps fall inside the window; ledgers are always read by timestamp, then id.

Tables: `customers`, `kyc_profiles` (1:1), `accounts`, `merchants`, `devices`, `account_devices`
(N:M bridge, first/last seen), `account_status_history` (append-only events), `risk_rules`,
`transactions`, `transfer_links`, `alerts`, `alert_transactions` (N:M bridge), `cases`,
`case_alerts` (bridge), `analyst_actions` (append-only events). Every field, its type, whether it
is required, allowed values, units, uniqueness and validation rule is in
[docs/data_dictionary.md](docs/data_dictionary.md); relationships in [docs/er_diagram.md](docs/er_diagram.md).

## What is planted, detected and injected

| pattern | suspicious plant | benign look-alike (false positive) | rule |
|---|---|---|---|
| velocity spike | card testing at 3-6 merchants | many small payments at one TRANSPORT merchant | RULE-001 |
| new-device activity | new device + transfers to a never-paid account | new phone + payment to a known merchant | RULE-002 |
| circular transfer | ring across 3-5 customers | own-account sweep (one customer) | RULE-003 |
| dormant reactivation | online reactivation + deposit + rapid transfers out | in-branch reactivation + planned purchase | RULE-004 |
| high amount vs. profile | 8-14x profile max at gift-card/crypto/money-service/gambling | big-ticket planned purchases, 3.2-12x | RULE-005 |

Planted events are merged into the baseline event stream **before** ledger posting and before
any rule runs. Edge cases (10 of each at full scale, after the valid data is complete):
transaction on a closed account, negative balance without overdraft, transfer legs that do not
reconcile, alert without triggering transaction, disposition timestamp before the alert.

Results at full scale (seed 20261022, from `output/full/validation_summary.json`):

* exact counts 5,000 / 7,000 / 100,000 / 1,000; every account ledger reconciles (0 balance findings on clean data)
* every suspicious plant detected by its rule: 200 / 200; benign look-alikes detected 100 / 100 (rules 1-4), big-ticket 659 / 850 made the top 1,000
* validator: 0 findings on the clean data; 50 findings on the final data = exactly the 50 labelled edge cases
* dispositions vs. ground truth: suspicious → CONFIRMED 196 (4 not yet dispositioned); benign → FALSE_POSITIVE 616, ESCALATED 172, CONFIRMED 1
* false-positive rate per rule (dispositions): RULE-001 0.33, RULE-002 0.34, RULE-003 0.28, RULE-004 0.30, RULE-005 0.74

## Design decisions

* **Contract first.** `generator/schema.sql` was written before the generator; every table is built
  with exactly its columns, the validator introspects it with SQLite, and the ER diagram is generated
  from it (a test fails if the diagram or the dictionary drift from the schema).
* **One seeded generator, fixed order.** A single PCG64 `Generator` is created from `--seed` and
  passed through the stages in a fixed order; no wall clock, uuid4 or set-iteration order affects
  output. The reference calendar is constant (window 2026-01-01 – 2026-10-15, snapshot 2026-10-22).
* **Exact counts by budget.** Planted events are drawn first so their share of `--transactions` is
  known; the baseline fills the remainder minus 4 rows per edge-case index. Alerts are the top
  `--alerts` candidates; the run fails loudly if there are fewer candidates.
* **Derived, never random.** Balances come from the previous posting; statuses come from event
  tables; `last_seen`, trigger counts/amounts, scores, ranks, case summaries and statuses are computed
  from source rows. Money is held in integer cents; scores use exact decimal arithmetic.
* **Funding before planted debits.** Every planted scenario that debits an account first credits it,
  and baseline activity is kept 50 h away from planted events, so planted patterns cannot be
  distorted by random baseline activity.
* **Transparent scoring and ranking.** One formula for all rules; `reason` states facts and
  arithmetic; `priority_rank` is a total order (score, created_at, alert_id).
* **A score that never saturates.** `score = weight + (score_ceiling − weight) × (1 − 0.5^((metric −
  threshold) / (midpoint − threshold)))`: the rule's weight at its threshold, halfway to its ceiling at
  its `midpoint_value`, and every further step of the same size halves the remaining distance. A
  larger metric therefore always scores higher, every score stays on the same 0-100 range, and no
  alert reaches its ceiling. Scores are **comparable within a rule but not calibrated across rules**:
  how one rule's scores line up against another's comes from the hand-set weights, midpoints and
  ceilings, not from measured fraud rates. That is why the first 14 alerts are all RULE-002: its
  largest suspicious amounts (up to about EUR 6,900) run more than twice past its EUR 3,000
  midpoint. The tie-break (older alert first, then `alert_id`) only separates alerts with an
  identical score, typically velocity bursts with the same debit count.
* **Lower ceiling for the single-transaction rule.** The four pattern rules need several related
  events and approach 100. RULE-005 compares one payment with a self-declared KYC figure, where an
  extreme ratio more often means a stale profile than a criminal pattern, so it approaches 92.
  Without this, benign in-branch reactivations paying 15-38x a small profile maximum filled the whole
  top 10. RULE-005's midpoint (6x) keeps the smallest planted suspicious ratio (8x) at 80.35 or more,
  above the DR-2 confirmation bar of 80. The same 1,000 alerts are selected either way; only their
  order changes.
* **One rule dominates on purpose.** RULE-005 (high amount vs. profile) yields about 74 % of the
  alerts with a ~74 % false-positive rate, against ~30 % for the pattern rules. The generator plants
  benign big-ticket purchases equal to ~85 % of `--alerts` so that (a) there are more candidates than
  alerts and the top-N cut is really exercised, and (b) false positives are plentiful and measurable.
  This mirrors production, where profile-deviation rules are the noisiest. The share is set in
  `generator/patterns.py::plant_counts`.
* **A retired rule is kept.** `risk_rules` has six rows; RULE-006 (round-amount structuring) has
  `is_active = 0` and `effective_to = 2025-12-31`, so it is never evaluated and has no alerts. It is
  kept to show rule lifecycle and versioning, which is why per-rule queries list five rules.
* **Decisions from rules, not labels.** Dispositions apply DR-1..DR-3 to observable data only;
  the ground truth is written separately and an AST test proves no rule/feature module references it.
* **Single-defect edge cases.** Each injected defect breaks exactly one rule and only its own rows
  (verified by a full table diff), so the validator can be required to flag exactly that set.
* **Independent validator.** It imports nothing from the generator, reads only the exported CSVs and
  `schema.sql`, and restates the business rules itself.
* **Append-only history.** Status and action events are only appended; SQLite triggers reject
  UPDATE/DELETE on those tables.

## Development history

[docs/development_history.md](docs/development_history.md) explains how the work evolved: the
ten staged gates, the problem found at each gate and how it was fixed, and the review
iterations (checksum scope, non-saturating score, per-rule score ceilings). Each entry names the
code and the test that now guards it. The git history has one commit per stage in build order.

## Known limitations

* Behaviour is stylised: one currency (EUR), one country group, uniform time-of-day, simple
  per-type amount distributions; ~14 transactions per active account over the window.
* Weights, midpoints and ceilings are hand-set configuration, not calibrated on loss data. At full
  scale planted suspicious alerts make up 10 of the top 10, 34 of the top 50 and 64 of the top 100
  (the first 14 are all RULE-002 new-device cases, whose EUR metric runs furthest past its midpoint);
  the rest are benign look-alikes that DR-1 clears. After rounding, a score shows its ceiling only
  once the remaining distance is below 0.005 points (RULE-005: a ratio of about 41.6x or more), which
  no generated alert reaches.
* The baseline is deliberately quiet: baseline debits are capped at 2.5x the profile maximum, so
  RULE-005 hits come almost entirely from planted scenarios (hence its high false-positive rate).
* Natural (unplanted) rule hits are rare but possible and are labelled by the decision rule like any
  other alert; one such baseline new-device alert is CONFIRMED by DR-2 (a deliberate, measured
  decision error). Unlabelled alerts are counted as benign in the ground-truth metrics.
* A case groups alerts of one customer only; ring members owned by other customers are linked
  through the alert's triggering transactions, not through extra cases.
* `--alerts` must stay well below the number of rule hits the configuration can produce
  (roughly `--transactions` ≥ 20 x `--alerts`); the generator refuses otherwise.
* Byte-identical output is guaranteed for the same seed, parameters **and environment**; other
  numpy/pandas versions may change random streams or float formatting (versions are recorded in the
  manifest).
* Account freezes after confirmed fraud and escalation outcomes are out of scope (ESCALATED is terminal).

## Assumptions made

* Ground-truth column `issue_or_pattern_type` holds the pattern type for pattern rows and the issue
  type for edge-case rows; patterns are labelled on their anchor account.
* KYC, status-event and bridge identifiers not specified in the brief use `KYC-NNNNNN`,
  `ASH-NNNNNNNN` and composite primary keys.
* The starter profile injects 4 (not 10) edge cases per type so they stay a small share of 40 alerts;
  `--edge-cases` overrides both profiles.
* Transaction IDs follow posting order; edge-case rows are appended after the valid data and
  therefore carry the highest IDs.
