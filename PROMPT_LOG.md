# Prompt log

> About this log. AI responses are omitted. No tool in this environment exported the prompts automatically, so this record was kept manually.

> It contains the four prompts that specified and changed the application, in order and dated.


> Not included
> - five messages that pasted terminal output from local test runs, with at most a one-line question


> Fixes without a prompt of their own. Two of those test runs surfaced problems that were then fixed:
> - a stale local database file that broke the checksum comparison
> - benign RULE-005 outliers leading the alert queue, which led to the per-rule score ceilings
> (RULE-005 capped at 92) that prompt 4 refers to

| # | Date | Purpose |
|---|---|---|
| 1 | 2026-11-01 | Full specification: business context, constraints, entities, ID formats, business logic, edge cases, ground truth, outputs, ten staged test gates, final acceptance check |
| 2 | 2026-11-01 | Gaps to verify after the first delivery (full-scale validation, reproducibility, all five edge cases, deliverables), issues to fix, and a from-scratch verification sequence |
| 3 | 2026-11-02 | Remaining issues: score saturation, prominence of the RULE-005 rationale, appended edge-case IDs |
| 4 | 2026-11-02 | Minor issues: cross-rule score calibration, RULE-005 share of alerts |

## Prompt 1 — 2026-11-01

```text
ROLE
You are a senior data engineer. Build a reproducible SYNTHETIC data generator for a
fraud / AML-style analyst-review prioritization workflow. Work in strict stages. After
EVERY stage, write and run automated tests, show me the real command output, and fix any
failure before starting the next stage. Never claim something passes without running it.
If a test fails twice, stop and explain instead of weakening the test.

BUSINESS CONTEXT
Prioritize transactions and linked accounts for analyst review using transparent rules
and synthetic risk indicators. Actors: customer/account holder, operations analyst,
risk/compliance reviewer, system administrator. The output must expose key decisions and
exceptions, keep traceability, and produce measurable outputs. Synthetic data only. No
real personal data and no external data. State that in the source manifest.

TECH CONSTRAINTS
- Python 3.11+, only: standard library, numpy, pandas, pytest. No Faker, no network calls.
- One generator, parameterized: --seed (default 20261022), --customers, --accounts,
  --transactions, --alerts, --out. Defaults are the full scale (5000 / 7000 / 100000 /
  1000). A --profile starter (200 / 280 / 4000 / 40) must use the SAME code path.
- Use a single numpy Generator seeded from --seed. No wall-clock time, uuid4 or
  unordered set iteration anywhere that affects output. Output must be byte-identical
  across runs.
- Layout: generator/ (one module per stage), tests/ (one test file per stage),
  output/, docs/, README.md, PROMPT_LOG.md, requirements.txt, Makefile (optional).

ENTITIES (every one needs a primary key and explicit foreign keys)
Customer, KYCProfile (1:1 with Customer), Account, Merchant, Device,
AccountDevice (bridge, N:M Account-Device, with first_seen/last_seen),
Transaction, TransferLink, RiskRule, Alert, AlertTransaction (bridge, N:M),
Case, CaseAlert (bridge), AnalystAction, AccountStatusHistory (event table).
Document cardinality, optional relationships, effective dates and lifecycle states for each.

ID FORMATS (zero-padded, immutable, identical everywhere they are referenced)
CUS-000001 | ACC-CUS-000001-01 (the -NN suffix is the account sequence for that
customer) | TXN-2026-00000001 | ALT-2026-000001 | CASE-2026-000001 |
MER-000001 | DEV-000001 | TRF-2026-000001 | RULE-001 | ACT-2026-000001

BUSINESS LOGIC
1. Create master data first: customers + KYC profiles, accounts (ownership, status
   lifecycle, open date, optional overdraft_limit), merchants, devices.
2. Generate timestamped debit/credit transactions per account in chronological order.
   balance_after = balance_before +/- amount, derived from the previous row, never
   random. No negative balance unless the account has an overdraft limit that covers it.
3. Transfers: each TransferLink points to a source debit txn and a destination credit
   txn with the same amount, and both legs exist as transactions.
4. State changes are append-only event rows (e.g. AccountStatusHistory). Never
   overwrite history.
5. Rules engine: RiskRule table with transparent, documented rules and weights, e.g.
   velocity spike, new-device activity, circular transfer, dormant-account
   reactivation, high amount vs. customer profile. Evaluate the rules over the data to
   produce alerts. Each alert stores rule_id, a score, a human-readable reason and its
   triggering transactions (via AlertTransaction). Alert ranking = score, with a
   deterministic tie-break, and must be explainable.
6. Group related alerts into Cases (same account/customer within a time window).
   Record AnalystActions (assign, review, disposition: confirmed / false_positive /
   escalated). Dispositions come from a documented decision rule applied to the alert,
   not from random labels.
7. Inject planted patterns BEFORE rule evaluation (velocity spikes, new-device activity,
   circular transfer rings, dormant reactivation) and ALSO ensure false positives exist
   (rule-triggering but benign behaviour). Final alert count must equal the --alerts
   parameter (take the top-N by score if there are more candidates).
8. All derived values (balances, totals, scores, statuses, dates) are computed from
   source records.

EDGE CASES (inject AFTER valid baseline data, each labelled, small fixed count, e.g. 10 each)
- transaction references a closed account
- negative balance without overdraft
- transfer legs do not reconcile
- alert has no triggering transaction
- case disposition timestamp precedes the alert timestamp
Only the targeted rows may be touched. Do not corrupt unrelated rows.

GROUND TRUTH
Write a separate ground_truth.csv (+ .json): entity_type, entity_id, issue_type /
pattern_type, expected_label, notes. It must never be imported by the generator's feature
or rule code (add a test that checks this) and must not be a column in any input table.

OUTPUTS (in output/)
CSV for every table, JSON versions, schema.sql + seed.sql (SQLite-compatible, loadable),
ground_truth.csv/json, source_manifest.json (all synthetic, no external sources,
seed, generator version), validation_summary.json + .md, checksums.sha256.
Docs: docs/data_dictionary.md covering every field of every entity with type,
required/optional, allowed values, units, uniqueness and validation rule; plus an ER
diagram (Mermaid) in docs/.

STAGES, each one ends with tests + a gate (do NOT skip ahead)
Stage 1  Project skeleton, config/CLI, seeded RNG, ID helpers.
         Tests: ID formats, determinism of the RNG, CLI parsing, scale parameters.
Stage 2  Data dictionary + schema.sql (written before the generator code).
         Tests: schema loads in SQLite; every entity has a PK and every FK is declared.
Stage 3  Master data (customers, KYC, accounts, merchants, devices, bridges, status history).
         Tests: counts match parameters, unique PKs, valid FKs, allowed enums, date order, lifecycle.
Stage 4  Transactions + transfers (baseline, valid only).
         Tests: running balance reconciles for every account, no unexplained negative
         balances, transfer legs match, no txn on a closed account, distributions are
         sensible (print summary stats).
Stage 5  Planted patterns + rules engine + alerts + cases + analyst actions.
         Tests: every alert has rule_id + score + reason + at least one triggering txn;
         each planted pattern is detected by its rule; false positives are present;
         ranking is deterministic; alert count equals the parameter; case/disposition
         dates are ordered.
Stage 6  Edge-case injection + ground_truth.
         Tests: each injected case is present, labelled in ground truth and nothing else was touched
         (diff the other rows); ground truth is not imported anywhere in the rules code.
Stage 7  Independent validator (separate module). Checks PK uniqueness, FK integrity,
         enums, date ordering, arithmetic reconciliation, lifecycle transitions,
         duplicates. It must FLAG exactly the injected edge cases and nothing else.
         Writes validation_summary.
         Tests: validator on clean data = 0 errors; with injected data = exactly the
         expected set of findings.
Stage 8  Exports (CSV / JSON / SQL / manifest / checksums / docs).
         Tests: SQL loads into SQLite and row counts match the CSVs; JSON matches CSV.
Stage 9  Reproducibility: run the full generation twice with the same seed into two
         folders, compare row counts and SHA-256 checksums; also run once with a
         different seed and show that the checksums differ. Write the result to
         docs/reproducibility.md.
Stage 10 README.md with: overview, folder structure, requirements, install, the exact
         generator commands (starter and full), how to run tests and validation, how to
         reproduce, fixed seed, output file list, design decisions, known limitations.
         Create PROMPT_LOG.md containing every prompt I send you in this project
         (verbatim, numbered, with date). Add a note at the top that AI responses are
         omitted and, if any tool could not export prompts, that the record was kept
         manually.

FINAL ACCEPTANCE CHECK (run and show output)
- full pytest suite green
- full-scale generation succeeds with the exact counts: 5000 / 7000 / 100000 / 1000
- balances reconcile, every alert has triggering facts and rules, planted patterns are
  detected, ranking is explainable, false positives are represented
- reproducibility proven by checksums

FINAL RESPONSE FORMAT
End with a section called "MANUAL RUN COMMANDS": copy-pasteable commands in order, for
fresh setup on my machine (create venv, install, generate starter, generate full, run
tests, run validator, reproducibility check, load SQL into SQLite and run 3 sample
queries such as the top 10 alerts by score and the false-positive rate per rule). Say
which OS shell they are for and give both Windows (PowerShell) and Linux/macOS versions.
Also list any assumptions you made.

WORKING RULES
- Print test output after every stage. Do not summarize failures away.
- Prefer simple, readable code with docstrings; no dead code.
- Ask me only if something is truly blocking. Otherwise state the assumption and continue.
```

## Prompt 2 — 2026-11-01

```text
Not yet verified

1. Full-scale validation. You only validated the starter profile (20 findings, 20 expected). Run the validator on `output/full` and confirm the findings exactly match the injected edge cases there too.
2. Reproducibility. The Stage 9 tests passed, but the checksum comparison output isn’t shown. Same seed should give identical checksums, and a different seed should give different ones.
3. Three of the five edge cases. Q4 only shows negative balance and closed account. Confirm that unreconciled transfer legs, an alert with no triggering transaction, and a case disposition before its alert are each present and labelled in `ground_truth.csv`.
4. Deliverables. Check that these actually exist: README, `PROMPT_LOG.md`, `schema.sql`, `seed.sql`, `source_manifest.json`, `validation_summary`, `checksums.sha256`, and the data dictionary.

Issues

* Score saturation: the top 10 alerts all score exactly 100.0, so the ranking in Q1 comes down to the tie-break. The brief asks for explainable ranking. Consider a score that doesn’t cap, or show the tie-break rule in the docs.
* One rule dominates: `HIGH_AMOUNT_VS_PROFILE` produces about 73% of all alerts, with a 73.6% false-positive rate against about 30% for the others. The planted patterns have only about 64 alerts each. It’s defensible, but a more balanced mix looks more realistic. Document it as a deliberate design choice if you keep it.
* A sixth rule with no alerts: `risk_rules=6`, but Q2 shows only five rules. Check what the sixth is and whether it fires, or explain why it doesn’t.
* Injected IDs: the injected transactions (TXN...99961 to 99980) have the highest IDs but earlier timestamps. That’s fine if documented as appended edge cases, but mention it in the README.
* Repeated pytest sections: stages 5, 6 and 7 appear several times in the pytest output. This is probably parametrised fixtures regrouping the tests, so it’s harmless, but confirm no test runs twice by accident.

Give me every command, in order, to verify all of this from a fresh start. Assume I have everything installed but have never tested the project before.
```

## Prompt 3 — 2026-11-02

```text
Remaining issues

* 306 of 1000 alerts are capped at a score of 100, so the top ranks are decided by the tie-break (older first, then alert ID). It’s documented and deterministic, so it satisfies “explainable”, but a score that doesn’t saturate would look more polished.
* RULE-005 produces about 74% of alerts, and its false-positive rate is 73.6% against roughly 30% for the others. The README explains this as deliberate, so keep that wording prominent.
* The injected edge-case transaction IDs are the highest ones but have earlier timestamps. The acceptance check documents this, so mention it in the README too if it isn’t already there.
```

## Prompt 4 — 2026-11-02

```text
Minor issues (none are blockers)

* The top 12 are all RULE-002 alerts, because its raw metric is a large amount and scales high, while RULE-005 is capped at 92. State that scores are comparable within a rule but not calibrated across rules, and document this as a design choice.
* RULE-005 still produces about 74% of alerts, with a false-positive rate of about 74%. This is documented as deliberate in the README.
```
