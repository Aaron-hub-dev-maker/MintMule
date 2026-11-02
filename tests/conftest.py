"""Shared fixtures. Generated data is cached per session so each scale is built once."""

import pytest

from generator import master_data, transactions
from generator.config import parse_config
from generator.pipeline import build
from generator.rng import make_rng


@pytest.fixture(scope="session", params=["starter", "full"])
def master(request):
    """(config, master tables) for both scale profiles."""
    cfg = parse_config(["--profile", request.param])
    return cfg, master_data.generate(cfg, make_rng(cfg.seed))


@pytest.fixture(scope="session", params=["starter", "full"])
def baseline(request):
    """(config, tables) with master data plus a valid baseline ledger of exactly --transactions rows."""
    cfg = parse_config(["--profile", request.param])
    rng = make_rng(cfg.seed)
    tables = master_data.generate(cfg, rng)
    events = transactions.baseline_events(tables, rng, cfg.transactions)
    tables["transactions"], tables["transfer_links"], _ = transactions.post_ledger(tables, events)
    transactions.derive_device_last_seen(tables)
    return cfg, tables


@pytest.fixture(scope="session", params=["starter", "full"])
def dataset(request):
    """Complete pipeline output (clean snapshot, injected tables, ground truth) for both scales."""
    return build(parse_config(["--profile", request.param]))


@pytest.fixture(scope="session")
def clean(dataset):
    """(config, CleanDataset): planted patterns, alerts, cases and actions, before edge cases."""
    return dataset.cfg, dataset.clean
