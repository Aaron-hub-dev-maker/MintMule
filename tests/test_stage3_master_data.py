"""Stage 3 gate: master data counts, unique PKs, valid FKs, enums, date order and lifecycle."""

import numpy as np
import pandas as pd

from generator import master_data
from generator.config import AS_OF, parse_config
from generator.master_data import REVIEW_DAYS, WINDOW_START_S
from generator.patterns import plant_counts
from generator.rng import make_rng
from generator.timeutil import DAY, to_seconds
from helpers import assert_table_integrity

MASTER_TABLES = ["customers", "kyc_profiles", "accounts", "merchants", "devices",
                 "account_devices", "account_status_history"]
ALLOWED_TRANSITIONS = {
    (None, "ACTIVE"): {"ACCOUNT_OPENED"},
    ("ACTIVE", "DORMANT"): {"INACTIVITY_180_DAYS"},
    ("DORMANT", "ACTIVE"): {"REACTIVATED_ONLINE", "REACTIVATED_IN_BRANCH"},
    ("ACTIVE", "CLOSED"): {"CUSTOMER_REQUEST"},
    ("DORMANT", "CLOSED"): {"CUSTOMER_REQUEST"},
}


def test_counts_match_parameters(master):
    cfg, t = master
    assert len(t["customers"]) == cfg.customers
    assert len(t["kyc_profiles"]) == cfg.customers
    assert len(t["accounts"]) == cfg.accounts
    assert len(t["merchants"]) >= 63 and len(t["devices"]) > 0 and len(t["account_devices"]) > 0


def test_pk_fk_enums_required_and_id_formats(master):
    _, t = master
    assert_table_integrity(t, MASTER_TABLES)


def test_kyc_is_one_to_one_with_customers(master):
    _, t = master
    kyc = t["kyc_profiles"]
    assert kyc["customer_id"].is_unique
    assert set(kyc["customer_id"]) == set(t["customers"]["customer_id"])
    assert (kyc["kyc_id"].str[4:] == kyc["customer_id"].str[4:]).all()


def test_account_ids_embed_owner_and_consecutive_sequence(master):
    _, t = master
    acc = t["accounts"]
    assert (acc["account_id"].str[4:14] == acc["customer_id"]).all()
    assert (acc["account_id"].str[-2:].astype(int) == acc["account_seq"]).all()
    for _, seqs in acc.groupby("customer_id")["account_seq"]:
        assert sorted(seqs) == list(range(1, len(seqs) + 1))
    assert set(t["customers"]["customer_id"]) == set(acc["customer_id"]), "customer without account"


def test_customer_and_kyc_attribute_consistency(master):
    _, t = master
    cust = t["customers"].merge(t["kyc_profiles"], on="customer_id")
    business = cust["customer_type"] == "BUSINESS"
    assert cust.loc[business, "birth_year"].isna().all()
    assert cust.loc[~business, "birth_year"].between(1940, 2006).all()
    assert cust.loc[business, "segment"].isin(["SMALL_BUSINESS", "CORPORATE"]).all()
    assert cust.loc[~business, "segment"].isin(["RETAIL", "AFFLUENT"]).all()
    assert cust.loc[business, "occupation_or_industry"].isin(master_data.BUSINESS_INDUSTRIES).all()
    assert cust.loc[~business, "occupation_or_industry"].isin(master_data.INDIVIDUAL_OCCUPATIONS).all()
    assert (cust["expected_max_txn_amount"] < cust["expected_monthly_volume"]).all()
    assert (cust.loc[cust["pep_flag"] == 1, "risk_rating"] == "HIGH").all()


def test_account_product_rules(master):
    _, t = master
    acc = t["accounts"].merge(t["customers"][["customer_id", "customer_type"]], on="customer_id")
    first = acc[acc["account_seq"] == 1]
    assert (first["account_type"] == np.where(first["customer_type"] == "BUSINESS", "BUSINESS_CURRENT", "CURRENT")).all()
    assert (acc.loc[acc["account_type"] == "BUSINESS_CURRENT", "customer_type"] == "BUSINESS").all()
    assert acc.loc[acc["account_type"] == "SAVINGS", "overdraft_limit"].isna().all()
    assert (acc["overdraft_limit"].dropna() > 0).all()
    assert (acc["opening_balance"] >= 0).all()


def test_opening_balance_follows_lifecycle(master):
    _, t = master
    acc = t["accounts"]
    hist = t["account_status_history"]
    opened = hist[hist["reason_code"] == "ACCOUNT_OPENED"].set_index("account_id")["event_timestamp"]
    closed = hist[hist["to_status"] == "CLOSED"].set_index("account_id")["event_timestamp"]
    open_ts = acc["account_id"].map(opened)
    close_ts = acc["account_id"].map(closed)
    zero_expected = (open_ts >= WINDOW_START_S) | (close_ts < WINDOW_START_S)
    assert (acc.loc[zero_expected, "opening_balance"] == 0).all()
    assert (acc.loc[~zero_expected, "opening_balance"] >= 1_000).all()


def test_date_ordering(master):
    _, t = master
    acc = t["accounts"].merge(t["customers"][["customer_id", "onboarding_date"]], on="customer_id")
    assert (acc["open_date"] >= acc["onboarding_date"]).all()
    assert (acc.loc[acc["account_seq"] == 1, "open_date"] == acc.loc[acc["account_seq"] == 1, "onboarding_date"]).all()
    assert (acc["open_date"] <= to_seconds("2026-06-30")).all()
    closed = acc.dropna(subset=["close_date"])
    assert (closed["close_date"] >= closed["open_date"]).all()

    kyc = t["kyc_profiles"].merge(t["customers"][["customer_id", "onboarding_date"]], on="customer_id")
    assert (kyc["verified_date"] >= kyc["onboarding_date"]).all()
    review = kyc["risk_rating"].map(REVIEW_DAYS) * DAY
    assert (kyc["next_review_date"] == kyc["verified_date"] + review).all()

    hist = t["account_status_history"]
    assert hist["event_timestamp"].between(to_seconds("2016-01-01"), to_seconds(AS_OF)).all()
    ordered = hist.sort_values(["account_id", "event_timestamp"])
    assert (ordered.groupby("account_id")["event_timestamp"].diff().dropna() > 0).all()

    links = t["account_devices"].merge(t["devices"], on="device_id")
    assert (links["first_seen"] >= links["first_registered_at"]).all()
    assert (links["last_seen"] >= links["first_seen"]).all()
    opened = hist[hist["reason_code"] == "ACCOUNT_OPENED"].set_index("account_id")["event_timestamp"]
    assert (links["first_seen"] >= links["account_id"].map(opened)).all()
    closed_ts = hist[hist["to_status"] == "CLOSED"].set_index("account_id")["event_timestamp"]
    bound = links["account_id"].map(closed_ts)
    assert (links.loc[bound.notna(), "first_seen"] < bound.dropna()).all()


def test_status_lifecycle_is_valid_and_statuses_are_derived(master):
    _, t = master
    hist = t["account_status_history"].sort_values(["account_id", "event_timestamp", "status_event_id"])
    for acc, group in hist.groupby("account_id"):
        prev = None
        for i, ev in enumerate(group.itertuples(index=False)):
            assert (ev.from_status if isinstance(ev.from_status, str) else None) == prev, acc
            assert ev.reason_code in ALLOWED_TRANSITIONS[(prev, ev.to_status)], (acc, prev, ev.to_status)
            assert (i == 0) == (prev is None)
            prev = ev.to_status
    last = hist.groupby("account_id")["to_status"].last()
    acc = t["accounts"]
    assert (acc["account_status"] == acc["account_id"].map(last)).all()
    assert (acc["close_date"].notna() == (acc["account_status"] == "CLOSED")).all()
    opened = hist[hist["reason_code"] == "ACCOUNT_OPENED"].set_index("account_id")["event_timestamp"]
    assert (acc["open_date"] == acc["account_id"].map(opened) // DAY * DAY).all()

    expected = acc.groupby("customer_id")["account_status"].agg(
        lambda s: "ACTIVE" if (s == "ACTIVE").any() else ("DORMANT" if (s == "DORMANT").any() else "CLOSED"))
    cust = t["customers"]
    assert (cust["customer_status"] == cust["customer_id"].map(expected)).all()
    kyc = t["kyc_profiles"]
    overdue = kyc["next_review_date"] < to_seconds(AS_OF[:10])
    assert (kyc["kyc_status"] == np.where(overdue, "REVIEW_OVERDUE", "CURRENT")).all()


def test_lifecycle_mix_supports_later_stages(master):
    cfg, t = master
    counts = plant_counts(cfg)
    status = t["accounts"]["account_status"].value_counts()
    hist = t["account_status_history"]
    in_window_closures = ((hist["to_status"] == "CLOSED") & (hist["event_timestamp"] >= WINDOW_START_S)).sum()
    assert status.get("DORMANT", 0) >= counts["suspicious_per_pattern"] + counts["lookalike_per_rule"]
    assert in_window_closures >= cfg.edge_cases
    multi = t["accounts"].groupby("customer_id").size()
    assert (multi >= 3).sum() >= counts["lookalike_per_rule"]


def test_reference_data_rules(master):
    _, t = master
    merch = t["merchants"]
    assert set(merch["category"]) == set(master_data.MERCHANT_CATEGORIES)
    elevated = merch["category"].isin(master_data.ELEVATED_CATEGORIES)
    assert (merch["risk_tier"] == np.where(elevated, "ELEVATED", "STANDARD")).all()
    assert (merch["onboarded_date"] < WINDOW_START_S).all()
    dev = t["devices"]
    os_ok = {"MOBILE_IOS": {"IOS"}, "MOBILE_ANDROID": {"ANDROID"}, "TABLET": {"IOS", "ANDROID"},
             "DESKTOP_WEB": {"WINDOWS", "MACOS", "LINUX"}}
    assert all(o in os_ok[d] for d, o in zip(dev["device_type"], dev["os_family"]))
    assert set(dev["device_id"]) == set(t["account_devices"]["device_id"]), "device without account link"
    owners = t["account_devices"]["account_id"].str[4:14]
    per_device = owners.groupby(t["account_devices"]["device_id"]).nunique()
    assert (per_device == 1).all(), "baseline devices belong to exactly one customer"


def test_master_data_is_deterministic():
    cfg = parse_config(["--profile", "starter"])
    a = master_data.generate(cfg, make_rng(cfg.seed))
    b = master_data.generate(cfg, make_rng(cfg.seed))
    c = master_data.generate(cfg, make_rng(cfg.seed + 1))
    for name in a:
        pd.testing.assert_frame_equal(a[name], b[name])
    assert not a["customers"].equals(c["customers"])


def test_print_master_summary(master):
    cfg, t = master
    acc = t["accounts"]
    print(f"\n[{cfg.profile}] rows: " + ", ".join(f"{k}={len(v)}" for k, v in t.items()))
    print(f"[{cfg.profile}] account_status: {acc['account_status'].value_counts().to_dict()}")
    print(f"[{cfg.profile}] account_type: {acc['account_type'].value_counts().to_dict()}")
    print(f"[{cfg.profile}] accounts per customer: "
          f"{acc.groupby('customer_id').size().value_counts().sort_index().to_dict()}")
    print(f"[{cfg.profile}] overdraft accounts: {acc['overdraft_limit'].notna().sum()}; "
          f"opening balance EUR median={acc['opening_balance'].median() / 100:.2f}")
