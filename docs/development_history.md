# Development history

How the generator was built, what went wrong along the way and how each problem was fixed.
Every fix points to the code that implements it and the test that now guards it, so the
reasoning can be followed in the code itself.

## How to read the code

* **One module per stage.** Each module docstring names its stage (e.g. `generator/rules.py`:
  "Stage 5b: transparent rules engine"); `generator/pipeline.py` lists the order of work and
  of draws from the seeded generator.
* **One test file per stage gate.** `tests/test_stage1_skeleton.py` … `tests/test_stage10_docs.py`;
  test names state the behaviour they prove (e.g. `test_running_balance_reconciles_for_every_account`).
  Running the stages in order replays the gates the build had to pass.
* **Contract first.** `generator/schema.sql` was written before any generator code; tables are
  built with exactly its columns, the ER diagram is generated from it and the validator
  introspects it.
* **Version control** was set up after development, when the work was packaged for
  submission. The commits group the final code by stage in build order (one commit per stage,
  then the acceptance script and the generated datasets); they do not replay the intermediate
  edits. This document records those edits.

## Timeline

| phase | date | what happened |
|---|---|---|
| Build | 2026-11-01 | Stages 1-10 built in order, each closed by its test gate |
| Review 1 | 2026-11-01 | Verification gaps closed; acceptance script and README design notes added |
| Local runs and review 2 | 2026-11-02 | Checksum scope fixed; non-saturating score; per-rule score ceilings |
| Review 3 | 2026-11-02 | Cross-rule calibration stated; README made step by step for a first run |

## 1. Staged build (2026-11-01)

The specification (prompt 1 in [`PROMPT_LOG.md`](../PROMPT_LOG.md)) fixed ten stages, each ending
with automated tests and a gate: no stage started before the previous one was green, and a test
that failed twice stopped the work for an explanation instead of being weakened.

| stage | delivered | problem found at the gate and its fix |
|---|---|---|
| 1 | CLI, `full` / `starter` profiles, single seeded PCG64 generator, ID helpers | – |
| 2 | `schema.sql` (15 tables, composite FKs, CHECKs, append-only triggers), data dictionary, generated ER diagram | `test_data_dictionary_documents_relationships_dates_and_lifecycle` failed twice, on two different tables. Work stopped as required, every section was checked, and the **dictionary** was completed; the test was not changed |
| 3 | customers, KYC, accounts, merchants, devices, account-device links, status history | – |
| 4 | baseline events, transfers, ledger posting with derived balances | bank-booked `TRANSFER_IN` credits carried a digital channel but no device. `channel_for` now books them on channel `SYSTEM` (`generator/transactions.py`); guarded by `test_channel_merchant_and_device_rules` |
| 5 | planted scenarios, rules engine, top-N alerts, cases, analyst actions | (a) a score of exactly 80.965 rounded differently in binary floating point, so scoring moved to exact `Decimal` arithmetic with half-up rounding (`rules.score`), and `test_scores_follow_the_documented_formula` became an exact comparison. (b) Every alert was dispositioned before the snapshot, so no case was OPEN; a 30 % review backlog (`cases.BACKLOG_SHARE`) and later scenario starts now leave some alerts OPEN / IN_REVIEW (`test_review_statuses_are_derived`) |
| 6 | single-defect edge-case injection on a copy, ground truth | – |
| 7 | independent validator (reads only CSV + `schema.sql`) | (a) an aggregate column named `last` clashed with the pandas `DataFrame.last` method when read as an attribute, so the aggregates were renamed. (b) The validator crashed on duplicate primary keys; it now de-duplicates before the business checks (`test_each_check_family_detects_a_crafted_defect`). (c) The mutation tests were slow and now run on the starter dataset only |
| 8 | CSV / JSON / SQL / manifest / checksums / docs, SQLite loader | – |
| 9 | run twice with one seed and once with another, compare checksums | – |
| 10 | README, prompt log | the docstring test found an undocumented class (`validator.TableContract`) |

The final full run then failed `test_ground_truth_is_never_imported_by_rule_or_feature_code`:
the validator reads the ground truth to write its reconciliation report. That use is legitimate
but must never influence a finding, so the test now names the few modules allowed to read the
ground truth (`GROUND_TRUTH_AWARE`), and a stricter test,
`test_validator_checks_are_blind_to_the_ground_truth`, proves that the validator's checks never
touch it.

## 2. Review 1: verification gaps (2026-11-01)

The first review (prompt 2) asked for evidence that the starter-scale output did not show.

* **Full-scale validation, reproducibility, all five edge cases, deliverables**: shown at full
  scale, and collected into one read-only report, `scripts/acceptance_check.py`, which prints the
  evidence and a PASS / FAIL verdict per acceptance point.
* **Rule mix**: RULE-005 produces about 74 % of the alerts on purpose (benign big-ticket purchases
  are planted so that the top-N cut is exercised); this is now stated at the top of the README.
* **The sixth rule**: RULE-006 is retired (`is_active = 0`) and kept for rule-lifecycle
  traceability (`test_inactive_rule_produces_no_alerts`).
* **Appended edge-case ids**: documented; acceptance check [9] shows the id and timestamp ranges.
* **Repeated pytest sections**: the parametrised starter / full fixtures regroup tests; acceptance
  check [10] proves that no test id is collected twice.
* Showing `created_at` in sample query Q1 introduced a semicolon inside an SQL comment, which split
  the query in two. The comment was reworded and `load_sqlite.split_queries` now only splits on a
  semicolon at the end of a line (`test_load_sqlite_cli_prints_query_results`).

## 3. Local runs and review 2 (2026-11-02)

* **Checksum scope.** A local run failed the reproducibility check: `aml_review.db`, built by
  `load_sqlite` inside the dataset folder, was being checksummed although the generator does not
  write it. `checksums.sha256` now covers only generator-owned files (`exports.write_checksums`);
  guarded by `test_stray_files_do_not_change_the_checksums`.
* **Score saturation.** 306 of 1,000 alerts were capped at 100, so the head of the queue was
  ordered by the tie-break. The capped score was replaced by a half-life curve:
  `score = weight + (ceiling - weight) × (1 - 0.5^((metric - threshold) / (midpoint - threshold)))`,
  with the `saturation_value` column renamed `midpoint_value` throughout the schema, rules engine,
  validator, dictionary and tests.
* **Per-rule score ceilings.** Without a cap, the most extreme outliers rose to the top: the top 10
  were all benign RULE-005 big-ticket payments. Candidate settings were measured against the full
  candidate list before choosing one; `risk_rules.score_ceiling` is now 100 for the four
  multi-event pattern rules and 92 for the single-transaction RULE-005 (midpoint 6×). The same
  1,000 alerts are selected; only their order changed. Guarded by
  `test_scores_stay_between_weight_and_ceiling`; acceptance check [6] shows the ground-truth share
  at the head of the queue.

| scoring version | alerts at the cap / ceiling | distinct scores | suspicious in top 10 / 50 / 100 |
|---|---|---|---|
| capped at 100 | 306 | – | 4 / – / – |
| half-life, common ceiling 100 | 0 | 746 | 0 / 28 / 50 |
| half-life, per-rule ceilings (current) | 0 | 765 | 10 / 34 / 64 |

## 4. Review 3 and packaging (2026-11-02)

* **Calibration across rules.** The README claimed one comparable score scale; it now states that
  scores are comparable within a rule but not calibrated across rules, which explains why the
  first 14 alerts are all RULE-002 (new-device activity).
* **First-run instructions.** The README's *Install, configure, run and verify* section walks a new
  user through setup with the expected result of every command.
* **Line endings.** `.gitattributes` keeps every file LF on checkout, so `checksums.sha256` still
  verifies after a clone on Windows.

## Results at each milestone

| milestone | tests | full-scale validation | reproducibility |
|---|---|---|---|
| end of staged build | all stage gates green | 50 findings = the 50 labelled edge cases | same seed identical, other seed different |
| after review 2 | 209 passed | unchanged | 41 / 41 files identical; checksum-file hash `7d05fc11…` |
