"""Manual acceptance check for a generated dataset folder (read-only).

Prints the evidence and a PASS / FAIL / INFO verdict for:

 1. full-scale validation: validator findings == the labelled edge cases
 2. reproducibility: checksums verify; same seed identical, other seed different
 3. all five edge cases present, labelled in ground_truth.csv and visible in the data
 4. deliverables exist (README, PROMPT_LOG, schema/seed SQL, manifest, summary, checksums, dictionary)
 5. exact row counts equal the requested parameters
 6. ranking: non-saturating score and the deterministic tie-break
 7. rule mix and false-positive rate per rule (INFO)
 8. the retired sixth rule (RULE-006)
 9. edge-case transactions are appended with the highest ids
10. no test is collected twice

Usage: python scripts/acceptance_check.py [dataset folder]   (default: output/full)
Run the generator and `python -m generator.reproducibility --profile full` first.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS: list[tuple[str, str]] = []


def section(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def verdict(name: str, ok: bool | None) -> None:
    status = "INFO" if ok is None else ("PASS" if ok else "FAIL")
    print(f"--> {status}")
    RESULTS.append((name, status))


def table(folder: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(folder / "csv" / f"{name}.csv", dtype=str, keep_default_na=False)


def python(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_checksums(path: Path) -> dict[str, str]:
    """relative path -> sha256 from a ``sha256sum``-format file."""
    pairs = (line.split("  ", 1) for line in path.read_text(encoding="utf-8").splitlines())
    return {rel: digest for digest, rel in pairs}


def check_validation(d: Path) -> None:
    section("[1] Validator on this dataset: findings must equal the labelled edge cases")
    proc = python("-m", "generator.validator", str(d))
    print(proc.stdout.strip() or proc.stderr.strip())
    summary = json.loads((d / "validation_summary.json").read_text(encoding="utf-8"))
    gt = pd.read_csv(d / "ground_truth.csv", dtype=str, keep_default_na=False)
    edge = gt[gt["category"] == "EDGE_CASE"]
    expected = set(zip(edge["issue_or_pattern_type"], edge["entity_id"]))
    flagged = {(f["check"], f["record_id"]) for f in summary["findings"]}
    print(f"\n{'issue type':<32}{'labelled':>10}{'flagged':>10}{'same ids':>10}")
    for issue in sorted(edge["issue_or_pattern_type"].unique()):
        want = {e for e in expected if e[0] == issue}
        got = {f for f in flagged if f[0] == issue}
        print(f"{issue:<32}{len(want):>10}{len(got):>10}{str(want == got):>10}")
    others = sorted({c for c, _ in flagged} - set(edge["issue_or_pattern_type"]))
    print(f"findings of any other check: {others or 'none'}")
    verdict("full-scale validation", proc.returncode == 0 and expected == flagged
            and len(summary["findings"]) == len(expected))


def check_reproducibility(d: Path) -> None:
    section("[2] Reproducibility: checksums verify; same seed identical; other seed different")
    listed = read_checksums(d / "checksums.sha256")
    bad = [rel for rel, digest in listed.items() if sha256(d / rel) != digest]
    files = {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()} - {"checksums.sha256"}
    other = sorted(files - set(listed))
    print(f"{d.as_posix()}/checksums.sha256: {len(listed)} files listed, {len(bad)} do not verify")
    print(f"  other files in the folder (not written by the generator, not checksummed): {other or 'none'}")
    runs = {name: ROOT / "output" / "reproducibility" / f"run_{name.lower()}" for name in "ABC"}
    if not all((p / "checksums.sha256").exists() for p in runs.values()):
        print("output/reproducibility/run_a..run_c not found: run "
              "`python -m generator.reproducibility --profile full` first")
        verdict("reproducibility", False)
        return
    digest = {name: sha256(p / "checksums.sha256") for name, p in runs.items()}
    digest["this folder"] = sha256(d / "checksums.sha256")
    seeds = {n: json.loads((p / "source_manifest.json").read_text(encoding="utf-8"))["seed"] for n, p in runs.items()}
    seeds["this folder"] = json.loads((d / "source_manifest.json").read_text(encoding="utf-8"))["seed"]
    for name, value in digest.items():
        print(f"  {name:<12} seed={seeds[name]}  sha256(checksums.sha256) = {value}")
    sums = {n: read_checksums(p / "checksums.sha256") for n, p in runs.items()}
    same_ab = sum(sums["A"][f] == sums["B"].get(f) for f in sums["A"])
    diff_ac = sum(sums["A"][f] != sums["C"].get(f) for f in sums["A"])
    print(f"  run A vs run B (same seed):  {same_ab}/{len(sums['A'])} files byte-identical")
    print(f"  run A vs run C (other seed): {diff_ac}/{len(sums['A'])} files differ "
          f"(identical ones are seed-independent: schema, docs, rule config)")
    verdict("reproducibility", not bad and digest["A"] == digest["B"] == digest["this folder"]
            and digest["A"] != digest["C"] and seeds["A"] == seeds["B"] != seeds["C"])


def check_edge_cases(d: Path) -> None:
    section("[3] All five edge cases: present, labelled in ground_truth.csv, visible in the data")
    per_type = json.loads((d / "source_manifest.json").read_text(encoding="utf-8"))["parameters"]["edge_cases_per_type"]
    gt = pd.read_csv(d / "ground_truth.csv", dtype=str, keep_default_na=False)
    txn = table(d, "transactions").set_index("transaction_id")
    accounts = table(d, "accounts").set_index("account_id")
    history = table(d, "account_status_history")
    closed_at = history[history["to_status"] == "CLOSED"].set_index("account_id")["event_timestamp"]
    links = table(d, "transfer_links").set_index("transfer_id")
    alerts = table(d, "alerts").set_index("alert_id")
    bridge = table(d, "alert_transactions")
    actions = table(d, "analyst_actions").set_index("action_id")

    def closed(i):
        t = txn.loc[i]
        return t.txn_timestamp > closed_at[t.account_id], f"{i} posted {t.txn_timestamp} on {t.account_id}, closed {closed_at[t.account_id]}"

    def negative(i):
        t = txn.loc[i]
        od = accounts.at[t.account_id, "overdraft_limit"]
        return float(t.balance_after) < 0 and od == "", f"{i} on {t.account_id}: balance_after {t.balance_after}, overdraft_limit '{od}'"

    def mismatch(i):
        link = links.loc[i]
        debit, credit = txn.loc[link.source_txn_id], txn.loc[link.dest_txn_id]
        ok = debit.amount == link.amount and float(credit.amount) != float(link.amount)
        return ok, f"{i}: link {link.amount}, debit leg {link.source_txn_id} {debit.amount}, credit leg {link.dest_txn_id} {credit.amount}"

    def orphan(i):
        n = int((bridge["alert_id"] == i).sum())
        return i in alerts.index and n == 0, f"{i}: alert exists, trigger_txn_count={alerts.at[i, 'trigger_txn_count']}, alert_transactions rows={n}"

    def early(i):
        a = actions.loc[i]
        created = alerts.at[a.alert_id, "created_at"]
        return a.action_type == "DISPOSITION" and a.action_timestamp < created, \
            f"{i}: DISPOSITION {a.disposition} at {a.action_timestamp}, alert {a.alert_id} created {created}"

    proofs = {"TXN_ON_CLOSED_ACCOUNT": closed, "NEGATIVE_BALANCE_NO_OVERDRAFT": negative,
              "TRANSFER_LEG_MISMATCH": mismatch, "ALERT_WITHOUT_TRIGGER": orphan, "DISPOSITION_BEFORE_ALERT": early}
    all_ok = True
    for issue, prove in proofs.items():
        rows = gt[(gt["category"] == "EDGE_CASE") & (gt["issue_or_pattern_type"] == issue)]
        checked = [prove(i) for i in rows["entity_id"]]
        ok = len(rows) == per_type and all(c[0] for c in checked)
        all_ok &= ok
        print(f"\n{issue}: {len(rows)} labelled (expected {per_type}), {sum(c[0] for c in checked)} proven in data,"
              f" label={sorted(set(rows['expected_label']))}")
        print(f"  ground truth: {rows.iloc[0]['ground_truth_id']} {rows.iloc[0]['entity_type']} "
              f"{rows.iloc[0]['entity_id']} - {rows.iloc[0]['notes']}")
        print(f"  data:         {checked[0][1]}")
    verdict("five edge cases", all_ok)


def check_deliverables(d: Path) -> None:
    section("[4] Deliverables exist and are not empty")
    paths = [ROOT / p for p in ("README.md", "PROMPT_LOG.md", "requirements.txt", "generator/schema.sql",
                                "docs/data_dictionary.md", "docs/er_diagram.md", "docs/risk_rules.md",
                                "docs/reproducibility.md")]
    paths += [d / p for p in ("sql/schema.sql", "sql/seed.sql", "source_manifest.json", "validation_summary.json",
                              "validation_summary.md", "checksums.sha256", "ground_truth.csv", "ground_truth.json",
                              "docs/data_dictionary.md", "docs/er_diagram.md")]
    ok = True
    for p in paths:
        size = p.stat().st_size if p.exists() else 0
        ok &= size > 0
        print(f"  {'ok ' if size else 'MISSING'} {size:>12,} bytes  {p.relative_to(ROOT).as_posix()}")
    for kind in ("csv", "json"):
        n = len(list((d / kind).glob(f"*.{kind}")))
        ok &= n == 15
        print(f"  {'ok ' if n == 15 else 'MISSING'} {n:>12} tables {(d / kind).relative_to(ROOT).as_posix()}/")
    manifest = json.loads((d / "source_manifest.json").read_text(encoding="utf-8"))
    print(f"  manifest: data_origin={manifest['data_origin']}, external_sources={manifest['external_sources']}, "
          f"personal_data={manifest['personal_data']}, seed={manifest['seed']}, version={manifest['generator_version']}")
    verdict("deliverables", ok and manifest["data_origin"] == "synthetic" and manifest["external_sources"] == [])


def check_counts(d: Path) -> None:
    section("[5] Row counts equal the requested parameters")
    manifest = json.loads((d / "source_manifest.json").read_text(encoding="utf-8"))
    ok = True
    for key in ("customers", "accounts", "transactions", "alerts"):
        rows = len(table(d, key))
        ok &= rows == manifest["parameters"][key]
        print(f"  {key:<13} requested {manifest['parameters'][key]:>7}   in csv {rows:>7}")
    verdict("exact counts", ok)


def check_ranking(d: Path) -> None:
    section("[6] Ranking: non-saturating score and the deterministic tie-break")
    rules = table(d, "risk_rules").set_index("rule_id")
    alerts = table(d, "alerts").assign(score_n=lambda f: pd.to_numeric(f["score"]))
    at_ceiling = int((alerts["score_n"] >= pd.to_numeric(alerts["rule_id"].map(rules["score_ceiling"]))).sum())
    sizes = alerts["score_n"].value_counts()
    print("  score ceilings: " + ", ".join(f"{r} {c}" for r, c in rules.loc[rules["is_active"] == "1",
                                                                          "score_ceiling"].items()))
    print(f"  alerts at their rule's ceiling: {at_ceiling}; distinct scores: {len(sizes)} "
          f"for {len(alerts)} alerts; largest group sharing one score: {int(sizes.max())}")
    print("  rule: priority_rank = order by score DESC, then created_at ASC (older first), then alert_id ASC")
    gt = pd.read_csv(d / "ground_truth.csv", dtype=str, keep_default_na=False)
    planted = gt[gt["expected_label"] == "SUSPICIOUS"]
    suspicious = set(zip(planted["expected_rule_id"], planted["entity_id"]))
    ordered = alerts.sort_values(["score_n", "created_at", "alert_id"], ascending=[False, True, True])
    ordered = ordered.assign(truth=["SUSPICIOUS" if k in suspicious else "-"
                                    for k in zip(ordered["rule_id"], ordered["account_id"])])
    print(f"  distinct scores among the top 10: {ordered.head(10)['score_n'].nunique()}")
    print("  planted suspicious (ground truth) among the top "
          + ", ".join(f"{k}: {int((ordered.head(k)['truth'] == 'SUSPICIOUS').sum())}" for k in (10, 50, 100)))
    print(f"\n  {'rank':>4}  {'score':>6}  {'metric':>12}  {'created_at':<20} {'alert_id':<16} {'rule':<9} truth")
    for row in ordered.head(12).itertuples(index=False):
        print(f"  {row.priority_rank:>4}  {row.score:>6}  {row.metric_value:>12}  {row.created_at:<20} "
              f"{row.alert_id:<16} {row.rule_id:<9} {row.truth}")
    verdict("ranking follows the documented order", ordered["priority_rank"].astype(int).tolist()
            == list(range(1, len(alerts) + 1)) and at_ceiling == 0)


def check_rule_mix(d: Path) -> None:
    section("[7] Rule mix and false-positive rate per rule (design information)")
    alerts = table(d, "alerts")
    actions = table(d, "analyst_actions")
    dispo = actions[actions["action_type"] == "DISPOSITION"].set_index("alert_id")["disposition"]
    gt = pd.read_csv(d / "ground_truth.csv", dtype=str, keep_default_na=False)
    plants = gt[gt["category"] != "EDGE_CASE"].groupby(["expected_rule_id", "expected_label"]).size()
    print(f"  {'rule':<9}{'alerts':>8}{'share':>8}{'dispositioned':>15}{'FP rate':>9}{'planted susp/benign':>22}")
    for rule, group in alerts.groupby("rule_id"):
        d_rule = dispo.reindex(group["alert_id"]).dropna()
        fp = (d_rule == "FALSE_POSITIVE").mean() if len(d_rule) else float("nan")
        print(f"  {rule:<9}{len(group):>8}{len(group) / len(alerts):>8.1%}{len(d_rule):>15}{fp:>9.3f}"
              f"{plants.get((rule, 'SUSPICIOUS'), 0):>12}/{plants.get((rule, 'BENIGN'), 0)}")
    print("  RULE-005 dominates on purpose: ~85% of --alerts benign big-ticket purchases are planted so the\n"
          "  top-N cut is exercised and false positives are plentiful; see README 'Design decisions'.")
    verdict("rule mix", None)


def check_retired_rule(d: Path) -> None:
    section("[8] The sixth rule")
    rules = table(d, "risk_rules")
    alerts = table(d, "alerts")
    for row in rules.itertuples(index=False):
        n = int((alerts["rule_id"] == row.rule_id).sum())
        print(f"  {row.rule_id} {row.rule_code:<26} is_active={row.is_active} effective "
              f"{row.effective_from}..{row.effective_to or 'open'}  alerts={n}")
    retired = rules[rules["rule_id"] == "RULE-006"].iloc[0]
    print(f"  RULE-006: {retired.description}")
    verdict("RULE-006 retired and silent", retired.is_active == "0" and retired.effective_to != ""
            and not (alerts["rule_id"] == "RULE-006").any())


def check_appended_ids(d: Path) -> None:
    section("[9] Edge-case transactions are appended after the valid data (highest ids)")
    gt = pd.read_csv(d / "ground_truth.csv", dtype=str, keep_default_na=False)
    links = table(d, "transfer_links").set_index("transfer_id")
    edge = gt[gt["category"] == "EDGE_CASE"]
    ids = set(edge.loc[edge["entity_type"] == "TRANSACTION", "entity_id"])
    for link in edge.loc[edge["entity_type"] == "TRANSFER_LINK", "entity_id"]:
        ids |= {links.at[link, "source_txn_id"], links.at[link, "dest_txn_id"]}
    txn = table(d, "transactions")
    appended = txn[txn["transaction_id"].isin(ids)]
    others = txn[~txn["transaction_id"].isin(ids)]
    print(f"  {len(appended)} edge-case transactions: {appended['transaction_id'].min()} .. "
          f"{appended['transaction_id'].max()}; valid data ends at {others['transaction_id'].max()}")
    print(f"  their timestamps range {appended['txn_timestamp'].min()} .. {appended['txn_timestamp'].max()}"
          " (ids follow posting order; ledgers are read by timestamp)")
    verdict("edge-case ids appended", appended["transaction_id"].min() > others["transaction_id"].max())


def check_tests() -> None:
    section("[10] Test collection: no test id appears twice")
    proc = python("-m", "pytest", "--collect-only", "-q")
    ids = [line for line in proc.stdout.splitlines() if "::" in line]
    dupes = [t for t, n in Counter(ids).items() if n > 1]
    per_file = Counter(t.split("::")[0] for t in ids)
    for path, n in sorted(per_file.items()):
        params = Counter("starter" if "[starter" in t else "full" if "[full" in t else "single" for t in ids
                         if t.startswith(path))
        print(f"  {n:>4}  {path}  {dict(params)}")
    print(f"  total {len(ids)} tests, {len(set(ids))} unique ids, duplicates: {dupes or 'none'}")
    print("  [starter]/[full] are the same test run on both scales; pytest groups them by dataset,"
          " which is why stage files appear in more than one block of the progress output.")
    verdict("no duplicated tests", bool(ids) and not dupes)


def main() -> int:
    folder = (ROOT / (sys.argv[1] if len(sys.argv) > 1 else "output/full")).resolve()
    for check in (check_validation, check_reproducibility, check_edge_cases, check_deliverables, check_counts,
                  check_ranking, check_rule_mix, check_retired_rule, check_appended_ids):
        check(folder)
    check_tests()
    section("SUMMARY")
    for name, status in RESULTS:
        print(f"  {status:<5} {name}")
    failed = [n for n, s in RESULTS if s == "FAIL"]
    print("\nALL CHECKS PASSED" if not failed else f"\nFAILED: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
