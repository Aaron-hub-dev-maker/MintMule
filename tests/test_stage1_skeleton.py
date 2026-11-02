"""Stage 1 gate: ID formats, RNG determinism, CLI parsing and scale parameters."""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from generator import ids
from generator.config import DEFAULT_SEED, PROFILES, Config, parse_config, validate_config
from generator.rng import make_rng, pick
from generator.timeutil import format_date, format_ts, next_hour, to_seconds

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# --- ID formats -------------------------------------------------------------------

@pytest.mark.parametrize("func, arg, expected", [
    (ids.customer_id, 1, "CUS-000001"),
    (ids.kyc_id, 42, "KYC-000042"),
    (ids.merchant_id, 1, "MER-000001"),
    (ids.device_id, 999999, "DEV-999999"),
    (ids.transaction_id, 1, "TXN-2026-00000001"),
    (ids.transfer_id, 1, "TRF-2026-000001"),
    (ids.rule_id, 1, "RULE-001"),
    (ids.alert_id, 1, "ALT-2026-000001"),
    (ids.case_id, 1, "CASE-2026-000001"),
    (ids.action_id, 1, "ACT-2026-000001"),
    (ids.status_event_id, 7, "ASH-00000007"),
])
def test_id_formats(func, arg, expected):
    assert func(arg) == expected


def test_account_id_embeds_customer_and_sequence():
    acc = ids.account_id("CUS-000001", 1)
    assert acc == "ACC-CUS-000001-01"
    assert ids.is_valid("account", acc)
    assert ids.customer_of_account(acc) == "CUS-000001"
    assert ids.account_id("CUS-004999", 12) == "ACC-CUS-004999-12"


@pytest.mark.parametrize("func, too_big", [
    (ids.customer_id, 1_000_000), (ids.transaction_id, 100_000_000), (ids.rule_id, 1000),
])
def test_id_overflow_is_rejected(func, too_big):
    with pytest.raises(ValueError):
        func(too_big)


@pytest.mark.parametrize("bad", [0, -1, 1.5, True])
def test_id_sequence_must_be_positive_integer(bad):
    with pytest.raises(ValueError):
        ids.customer_id(bad)


def test_account_id_rejects_bad_customer_or_sequence():
    with pytest.raises(ValueError):
        ids.account_id("CUS-1", 1)
    with pytest.raises(ValueError):
        ids.account_id("CUS-000001", 100)


def test_every_builder_output_matches_its_pattern():
    builders = {
        "customer": ids.customer_id(5), "kyc": ids.kyc_id(5), "merchant": ids.merchant_id(5),
        "device": ids.device_id(5), "transaction": ids.transaction_id(5), "transfer": ids.transfer_id(5),
        "rule": ids.rule_id(5), "alert": ids.alert_id(5), "case": ids.case_id(5),
        "action": ids.action_id(5), "status_event": ids.status_event_id(5),
        "account": ids.account_id("CUS-000005", 2),
    }
    assert set(builders) == set(ids.ID_PATTERNS)
    for kind, value in builders.items():
        assert ids.is_valid(kind, value), (kind, value)
    assert not ids.is_valid("transaction", "TXN-2026-1")
    assert not ids.is_valid("account", "ACC-000001-01")


# --- RNG determinism --------------------------------------------------------------

def test_same_seed_gives_identical_draws():
    a, b = make_rng(DEFAULT_SEED), make_rng(DEFAULT_SEED)
    assert np.array_equal(a.integers(0, 10**9, 1000), b.integers(0, 10**9, 1000))
    assert np.array_equal(a.normal(size=100), b.normal(size=100))
    assert pick(a, ["x", "y", "z"], size=20) == pick(b, ["x", "y", "z"], size=20)


def test_different_seed_gives_different_draws():
    assert not np.array_equal(make_rng(1).integers(0, 10**9, 100), make_rng(2).integers(0, 10**9, 100))


@pytest.mark.parametrize("bad", [-1, 1.5, "1", True])
def test_rng_rejects_invalid_seed(bad):
    with pytest.raises(ValueError):
        make_rng(bad)


def test_rng_pick_returns_python_objects():
    value = pick(make_rng(3), ["a", "b"])
    assert type(value) is str


# --- time helpers -----------------------------------------------------------------

def test_time_round_trip_and_hour_boundary():
    s = to_seconds("2026-03-04T10:15:30")
    assert format_ts(s) == "2026-03-04T10:15:30"
    assert format_date(s) == "2026-03-04"
    assert format_ts(next_hour(s)) == "2026-03-04T11:00:00"
    assert format_ts(next_hour(to_seconds("2026-03-04T11:00:00"))) == "2026-03-04T12:00:00"
    assert list(format_ts(np.array([0, 60]))) == ["1970-01-01T00:00:00", "1970-01-01T00:01:00"]


# --- CLI parsing and scale parameters ---------------------------------------------

def test_full_profile_is_default_scale():
    cfg = parse_config([])
    assert cfg.seed == 20261022
    assert cfg.scale() == {"customers": 5000, "accounts": 7000, "transactions": 100000, "alerts": 1000}
    assert cfg.edge_cases == 10
    assert cfg.profile == "full"
    assert cfg.out_dir == Path("output") / "full"


def test_starter_profile_scale():
    cfg = parse_config(["--profile", "starter"])
    assert cfg.scale() == {"customers": 200, "accounts": 280, "transactions": 4000, "alerts": 40}
    assert cfg.out_dir == Path("output") / "starter"


def test_explicit_arguments_override_profile():
    cfg = parse_config(["--profile", "starter", "--seed", "7", "--alerts", "50",
                        "--customers", "300", "--accounts", "400", "--out", "x/y", "--edge-cases", "3"])
    assert (cfg.seed, cfg.alerts, cfg.customers, cfg.accounts, cfg.edge_cases) == (7, 50, 300, 400, 3)
    assert cfg.transactions == PROFILES["starter"]["transactions"]
    assert cfg.out_dir == Path("x/y")


@pytest.mark.parametrize("argv", [
    ["--customers", "0"],
    ["--accounts", "4000"],                       # fewer accounts than customers
    ["--customers", "10", "--accounts", "1000"],  # more than 99 accounts per customer
    ["--seed", "-5"],
    ["--transactions", "100"],                    # too small for 1000 alerts
    ["--profile", "huge"],
    ["--alerts", "abc"],
])
def test_invalid_arguments_are_rejected(argv):
    with pytest.raises(SystemExit):
        parse_config(argv)


def test_validate_config_rejects_zero_edge_cases():
    with pytest.raises(ValueError):
        validate_config(Config(seed=1, customers=10, accounts=10, transactions=1000, alerts=5,
                               edge_cases=0, out_dir=Path("o"), profile="starter"))


def test_cli_entry_point_runs():
    result = subprocess.run([sys.executable, "-m", "generator", "--help"], cwd=PROJECT_ROOT,
                            capture_output=True, text=True, check=True)
    for flag in ("--seed", "--customers", "--accounts", "--transactions", "--alerts", "--out", "--profile"):
        assert flag in result.stdout
