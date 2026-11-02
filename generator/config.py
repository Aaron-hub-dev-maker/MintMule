"""Stage 1: run configuration, scale profiles, fixed reference calendar and CLI parsing.

The reference calendar is fixed so that no output ever depends on the wall clock.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SEED = 20261022

# Scale profiles. Both profiles run through exactly the same code path; a profile only
# supplies default values for the scale parameters.
PROFILES: dict[str, dict[str, int]] = {
    "full": {"customers": 5000, "accounts": 7000, "transactions": 100_000, "alerts": 1000, "edge_cases": 10},
    "starter": {"customers": 200, "accounts": 280, "transactions": 4000, "alerts": 40, "edge_cases": 4},
}

# Fixed reference calendar (UTC, ISO-8601, second resolution).
ID_YEAR = 2026                          # year embedded in TXN/ALT/CASE/TRF/ACT identifiers
HISTORY_START = "2016-01-01T00:00:00"   # earliest customer onboarding
WINDOW_START = "2026-01-01T00:00:00"    # first second of the transaction observation window
WINDOW_END = "2026-10-15T23:59:59"      # last second of the transaction observation window
AS_OF = "2026-10-22T00:00:00"           # data snapshot: no analyst action happens after this

MAX_ACCOUNTS_PER_CUSTOMER = 99          # the -NN account suffix has two digits


@dataclass(frozen=True)
class Config:
    """Resolved, validated parameters for one generator run."""

    seed: int
    customers: int
    accounts: int
    transactions: int
    alerts: int
    edge_cases: int
    out_dir: Path
    profile: str

    def scale(self) -> dict[str, int]:
        """Return the four headline scale parameters."""
        return {
            "customers": self.customers,
            "accounts": self.accounts,
            "transactions": self.transactions,
            "alerts": self.alerts,
        }


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser (python -m generator ...)."""
    parser = argparse.ArgumentParser(
        prog="python -m generator",
        description="Generate a reproducible synthetic fraud/AML analyst-review dataset.",
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), default="full",
                        help="scale profile supplying default counts (default: full)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"RNG seed (default: {DEFAULT_SEED})")
    parser.add_argument("--customers", type=int, help="number of customers (profile default if omitted)")
    parser.add_argument("--accounts", type=int, help="number of accounts (profile default if omitted)")
    parser.add_argument("--transactions", type=int, help="exact number of transactions in the final dataset")
    parser.add_argument("--alerts", type=int, help="exact number of alerts in the final dataset")
    parser.add_argument("--edge-cases", dest="edge_cases", type=int,
                        help="injected rows per edge-case type (full: 10, starter: 4)")
    parser.add_argument("--out", type=Path, help="output folder (default: output/<profile>)")
    return parser


def parse_config(argv: list[str] | None = None) -> Config:
    """Parse CLI arguments into a validated :class:`Config`.

    Explicit arguments override the selected profile's defaults.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    defaults = PROFILES[args.profile]
    values = {name: getattr(args, name) if getattr(args, name) is not None else default
              for name, default in defaults.items()}
    out_dir = args.out if args.out is not None else Path("output") / args.profile
    try:
        return validate_config(Config(seed=args.seed, out_dir=out_dir, profile=args.profile, **values))
    except ValueError as exc:
        parser.error(str(exc))
        raise  # unreachable: parser.error exits


def validate_config(cfg: Config) -> Config:
    """Raise ``ValueError`` when parameters are out of range; return ``cfg`` otherwise."""
    if cfg.seed < 0:
        raise ValueError("--seed must be >= 0")
    for name in ("customers", "accounts", "transactions", "alerts"):
        if getattr(cfg, name) < 1:
            raise ValueError(f"--{name} must be >= 1")
    if cfg.edge_cases < 1:
        raise ValueError("--edge-cases must be >= 1")
    if cfg.accounts < cfg.customers:
        raise ValueError("--accounts must be >= --customers (every customer owns at least one account)")
    if cfg.accounts > cfg.customers * MAX_ACCOUNTS_PER_CUSTOMER:
        raise ValueError(f"--accounts must be <= {MAX_ACCOUNTS_PER_CUSTOMER} x --customers")
    if cfg.transactions < 20 * cfg.alerts:
        raise ValueError("--transactions must be >= 20 x --alerts so that planted patterns fit the budget")
    return cfg
