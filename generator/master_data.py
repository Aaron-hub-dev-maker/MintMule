"""Stage 3: master data.

Creates customers with 1:1 KYC profiles, accounts (ownership, open date, optional
overdraft limit, opening balance), merchants, devices, account-device links and the
initial append-only account status history. Status columns on the master tables are
derived from the status history (``derive_statuses``), never drawn directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import ids
from .config import AS_OF, HISTORY_START, WINDOW_END, WINDOW_START, Config
from .rng import pick
from .tables import make_frame
from .timeutil import DAY, HOUR, MINUTE, day_start, to_seconds

WINDOW_START_S = to_seconds(WINDOW_START)
WINDOW_END_S = to_seconds(WINDOW_END)
AS_OF_S = to_seconds(AS_OF)
HISTORY_START_S = to_seconds(HISTORY_START)
LAST_OPEN_S = to_seconds("2026-06-30")         # no account opens after this date
LIFECYCLE_CUTOFF_S = to_seconds("2025-06-01")  # only older accounts can be dormant or closed

COUNTRIES = ["NL", "DE", "BE", "FR", "ES", "IT", "IE", "PT", "AT", "LU"]
COUNTRY_P = [0.22, 0.20, 0.10, 0.12, 0.10, 0.10, 0.05, 0.05, 0.04, 0.02]
SYLLABLES = ["ka", "lo", "mi", "ra", "te", "vi", "no", "sa", "du", "fe", "jo", "ri", "ta", "ze",
             "mo", "ba", "ne", "li", "po", "gu", "ve", "da", "si", "ko", "ma", "ru", "le", "tor"]
BUSINESS_SUFFIXES = ["Trading", "Holdings", "Services", "Logistics", "Studio", "Partners", "Works"]
INDIVIDUAL_OCCUPATIONS = ["EMPLOYED", "SELF_EMPLOYED", "RETIRED", "STUDENT", "UNEMPLOYED"]
INDIVIDUAL_OCCUPATION_P = [0.55, 0.12, 0.18, 0.08, 0.07]
BUSINESS_INDUSTRIES = ["RETAIL_TRADE", "PROFESSIONAL_SERVICES", "HOSPITALITY", "CONSTRUCTION",
                       "IT_SERVICES", "WHOLESALE"]
# Median expected monthly volume (EUR) per segment.
SEGMENT_MONTHLY_EUR = {"RETAIL": 2500, "AFFLUENT": 9000, "SMALL_BUSINESS": 30000, "CORPORATE": 150000}
REVIEW_DAYS = {"HIGH": 365, "MEDIUM": 730, "LOW": 1095}

MERCHANT_CATEGORIES = [
    "GROCERY", "RESTAURANT", "TRANSPORT", "FUEL", "UTILITIES", "TELECOM", "ONLINE_RETAIL",
    "GENERAL_RETAIL", "ELECTRONICS", "TRAVEL", "FURNITURE", "HOME_IMPROVEMENT", "AUTOMOTIVE",
    "INSURANCE", "HEALTH", "ENTERTAINMENT", "JEWELRY", "GIFT_CARDS", "CRYPTO_EXCHANGE",
    "MONEY_SERVICES", "GAMBLING",
]
MERCHANT_CATEGORY_P = [0.14, 0.12, 0.08, 0.07, 0.06, 0.04, 0.10, 0.08, 0.05, 0.04, 0.03, 0.03,
                       0.02, 0.03, 0.04, 0.03, 0.01, 0.01, 0.01, 0.005, 0.005]
# Category groupings (reference data used by planted scenarios and by the decision rule).
ELEVATED_CATEGORIES = ["GIFT_CARDS", "CRYPTO_EXCHANGE", "MONEY_SERVICES", "GAMBLING"]
PLANNED_PURCHASE_CATEGORIES = ["TRAVEL", "FURNITURE", "HOME_IMPROVEMENT", "AUTOMOTIVE", "ELECTRONICS", "INSURANCE"]
LOW_RISK_REPEAT_CATEGORIES = ["TRANSPORT", "FUEL", "GROCERY"]
CATEGORY_WORD = {
    "GROCERY": "Grocers", "RESTAURANT": "Kitchen", "TRANSPORT": "Transit", "FUEL": "Fuel",
    "UTILITIES": "Energy", "TELECOM": "Telecom", "ONLINE_RETAIL": "Online", "GENERAL_RETAIL": "Store",
    "ELECTRONICS": "Electronics", "TRAVEL": "Travel", "FURNITURE": "Furniture",
    "HOME_IMPROVEMENT": "Home & Garden", "AUTOMOTIVE": "Motors", "INSURANCE": "Insurance",
    "HEALTH": "Pharmacy", "ENTERTAINMENT": "Cinema", "JEWELRY": "Jewellers", "GIFT_CARDS": "Gift Cards",
    "CRYPTO_EXCHANGE": "Crypto Exchange", "MONEY_SERVICES": "Money Transfer", "GAMBLING": "Gaming",
}


def generate(cfg: Config, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Generate all master-data tables for ``cfg`` using the run's generator ``rng``."""
    customers, kyc = _customers_and_kyc(cfg.customers, rng)
    accounts, history = _accounts_and_history(customers, kyc, cfg.accounts, rng)
    merchants = _merchants(max(len(MERCHANT_CATEGORIES) * 3, cfg.customers // 10), rng)
    devices, links = _devices_and_links(customers, accounts, history, rng)
    tables = {
        "customers": customers, "kyc_profiles": kyc, "accounts": accounts, "merchants": merchants,
        "devices": devices, "account_devices": links, "account_status_history": history,
    }
    derive_statuses(tables)
    return tables


# --- customers and KYC ------------------------------------------------------------

def _names(rng: np.random.Generator, n: int, syllables: int) -> list[str]:
    """Build pronounceable synthetic name tokens from a fixed syllable list."""
    idx = rng.integers(0, len(SYLLABLES), size=(n, syllables))
    return ["".join(SYLLABLES[i] for i in row).capitalize() for row in idx]


def _customers_and_kyc(n: int, rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    is_business = rng.random(n) < 0.12
    first, last = _names(rng, n, 2), _names(rng, n, 3)
    suffixes = pick(rng, BUSINESS_SUFFIXES, size=n)
    display = [f"{l} {s}" if b else f"{f} {l}" for f, l, s, b in zip(first, last, suffixes, is_business)]
    birth_year = np.where(is_business, 0, rng.integers(1940, 2007, size=n))
    countries = pick(rng, COUNTRIES, size=n, p=COUNTRY_P)
    upmarket = rng.random(n)
    segment = np.where(is_business, np.where(upmarket < 0.2, "CORPORATE", "SMALL_BUSINESS"),
                       np.where(upmarket < 0.18, "AFFLUENT", "RETAIL"))
    # Individuals are onboarded on or after their 18th birthday.
    earliest = np.maximum(HISTORY_START_S, [to_seconds(f"{y + 18}-01-01") if y else 0 for y in birth_year])
    onboarding = np.array([day_start(int(rng.integers(lo, LAST_OPEN_S + 1))) for lo in earliest], dtype=np.int64)

    customer_ids = [ids.customer_id(i + 1) for i in range(n)]
    customers = make_frame("customers", {
        "customer_id": customer_ids,
        "customer_type": np.where(is_business, "BUSINESS", "INDIVIDUAL"),
        "display_name": display,
        "birth_year": pd.array([None if b else int(y) for b, y in zip(is_business, birth_year)], dtype="Int64"),
        "country_code": countries,
        "segment": segment,
        "onboarding_date": onboarding,
        "customer_status": [None] * n,
    })

    risk = np.array(pick(rng, ["LOW", "MEDIUM", "HIGH"], size=n, p=[0.65, 0.27, 0.08]), dtype=object)
    pep = rng.random(n) < 0.02
    risk[pep] = "HIGH"
    occupation = np.where(is_business, pick(rng, BUSINESS_INDUSTRIES, size=n),
                          pick(rng, INDIVIDUAL_OCCUPATIONS, size=n, p=INDIVIDUAL_OCCUPATION_P))
    median = np.array([SEGMENT_MONTHLY_EUR[s] for s in segment], dtype=float)
    monthly_eur = np.round(median * rng.lognormal(0.0, 0.4, size=n) / 50) * 50
    monthly_eur = np.maximum(monthly_eur, 500)
    max_eur = np.maximum(np.round(monthly_eur * rng.uniform(0.2, 0.4, size=n) / 10) * 10, 100)
    verified = np.array([day_start(int(rng.integers(o, LAST_OPEN_S + 1))) for o in onboarding], dtype=np.int64)
    next_review = verified + np.array([REVIEW_DAYS[r] for r in risk], dtype=np.int64) * DAY
    kyc = make_frame("kyc_profiles", {
        "kyc_id": [ids.kyc_id(i + 1) for i in range(n)],
        "customer_id": customer_ids,
        "risk_rating": risk,
        "pep_flag": pep.astype(int),
        "occupation_or_industry": occupation,
        "expected_monthly_volume": (monthly_eur * 100).astype(np.int64),
        "expected_max_txn_amount": (max_eur * 100).astype(np.int64),
        "verified_date": verified,
        "next_review_date": next_review,
        "kyc_status": [None] * n,
    })
    return customers, kyc


# --- accounts and status history --------------------------------------------------

def _accounts_per_customer(n_customers: int, n_accounts: int, rng: np.random.Generator) -> np.ndarray:
    """Split ``n_accounts`` over customers: one each plus a skewed share of the rest (max 99)."""
    weights = rng.gamma(0.6, 1.0, size=n_customers)
    counts = 1 + rng.multinomial(n_accounts - n_customers, weights / weights.sum())
    while (counts > 99).any():  # move any excess to the customers with the fewest accounts
        over = int(np.argmax(counts))
        counts[int(np.argmin(counts))] += counts[over] - 99
        counts[over] = 99
    return counts


def _office_time(rng: np.random.Generator, day: int) -> int:
    """A timestamp between 08:00 and 18:00 on ``day`` (midnight seconds)."""
    return int(day + rng.integers(8 * HOUR, 18 * HOUR))


def _accounts_and_history(customers: pd.DataFrame, kyc: pd.DataFrame, n_accounts: int,
                          rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    counts = _accounts_per_customer(len(customers), n_accounts, rng)
    monthly = kyc["expected_monthly_volume"].to_numpy()
    rows, events = [], []
    for c_idx, cust in enumerate(customers.itertuples(index=False)):
        business = cust.customer_type == "BUSINESS"
        extra_days = sorted(day_start(int(rng.integers(cust.onboarding_date, LAST_OPEN_S + 1)))
                            for _ in range(counts[c_idx] - 1))
        for seq, open_day in enumerate([int(cust.onboarding_date)] + extra_days, start=1):
            if seq == 1:
                acc_type = "BUSINESS_CURRENT" if business else "CURRENT"
            elif business:
                acc_type = pick(rng, ["BUSINESS_CURRENT", "SAVINGS"])
            else:
                acc_type = pick(rng, ["SAVINGS", "CURRENT"], p=[0.6, 0.4])
            acc_id = ids.account_id(cust.customer_id, seq)
            open_ts = _office_time(rng, open_day)
            events.append((acc_id, open_ts, None, "ACTIVE", "ACCOUNT_OPENED", "SYSTEM"))
            fate = _account_fate(rng, open_ts)
            events.extend(_fate_events(rng, acc_id, open_ts, fate))
            rows.append({
                "account_id": acc_id, "customer_id": cust.customer_id, "account_seq": seq,
                "account_type": acc_type, "currency": "EUR", "open_date": open_day, "close_date": None,
                "overdraft_limit": _overdraft(rng, acc_type),
                "opening_balance": _opening_balance(rng, acc_type, fate, open_ts, int(monthly[c_idx])),
                "account_status": None,
            })
    accounts = make_frame("accounts", {k: [r[k] for r in rows] for k in rows[0]})
    accounts["close_date"] = accounts["close_date"].astype("Int64")
    accounts["overdraft_limit"] = accounts["overdraft_limit"].astype("Int64")
    events.sort(key=lambda e: (e[1], e[0]))
    history = make_frame("account_status_history", {
        "status_event_id": [ids.status_event_id(i + 1) for i in range(len(events))],
        "account_id": [e[0] for e in events],
        "event_timestamp": np.array([e[1] for e in events], dtype=np.int64),
        "from_status": [e[2] for e in events],
        "to_status": [e[3] for e in events],
        "reason_code": [e[4] for e in events],
        "changed_by_role": [e[5] for e in events],
    })
    return accounts, history


def _account_fate(rng: np.random.Generator, open_ts: int) -> str:
    """Lifecycle path: ACTIVE, DORMANT (at window start), CLOSED_PRE, DORMANT_CLOSED_PRE or CLOSED_IN."""
    if open_ts >= LIFECYCLE_CUTOFF_S:
        return "ACTIVE"
    r = rng.random()
    if r < 0.028:
        return "CLOSED_PRE"
    if r < 0.04:
        return "DORMANT_CLOSED_PRE"
    if r < 0.10:
        return "DORMANT"
    if r < 0.15:
        return "CLOSED_IN"
    return "ACTIVE"


def _fate_events(rng: np.random.Generator, acc_id: str, open_ts: int, fate: str) -> list[tuple]:
    """Status events after opening for the chosen lifecycle path."""
    dormant = ("ACTIVE", "DORMANT", "INACTIVITY_180_DAYS", "SYSTEM")
    if fate == "DORMANT":
        lo = max(open_ts + 180 * DAY, to_seconds("2024-01-01"))
        return [(acc_id, int(rng.integers(lo, to_seconds("2025-12-15"))), *dormant)]
    if fate in ("CLOSED_PRE", "DORMANT_CLOSED_PRE"):
        close_ts = _office_time(rng, day_start(int(rng.integers(open_ts + 200 * DAY, to_seconds("2025-12-20")))))
        out = []
        if fate == "DORMANT_CLOSED_PRE":
            out.append((acc_id, int(rng.integers(open_ts + 180 * DAY, close_ts - 7 * DAY)), *dormant))
        prev = "DORMANT" if out else "ACTIVE"
        out.append((acc_id, close_ts, prev, "CLOSED", "CUSTOMER_REQUEST", "OPERATIONS_ANALYST"))
        return out
    if fate == "CLOSED_IN":
        day = day_start(int(rng.integers(to_seconds("2026-02-01"), to_seconds("2026-10-10"))))
        close_ts = int(day + rng.integers(10 * HOUR, 18 * HOUR))
        return [(acc_id, close_ts, "ACTIVE", "CLOSED", "CUSTOMER_REQUEST", "OPERATIONS_ANALYST")]
    return []


def _overdraft(rng: np.random.Generator, acc_type: str) -> int | None:
    """Optional overdraft limit in cents (current-type accounts only)."""
    if acc_type == "CURRENT" and rng.random() < 0.30:
        return int(pick(rng, [250, 500, 1000, 1500, 2000])) * 100
    if acc_type == "BUSINESS_CURRENT" and rng.random() < 0.40:
        return int(pick(rng, [2500, 5000, 10000, 20000])) * 100
    return None


def _opening_balance(rng: np.random.Generator, acc_type: str, fate: str, open_ts: int, monthly_cents: int) -> int:
    """Ledger balance (cents) at the start of the observation window or at opening if later."""
    if fate in ("CLOSED_PRE", "DORMANT_CLOSED_PRE") or open_ts >= WINDOW_START_S:
        return 0
    if fate == "DORMANT":
        return int(rng.integers(2_000, 150_000))
    low, high = {"CURRENT": (0.3, 1.5), "SAVINGS": (1.0, 6.0), "BUSINESS_CURRENT": (0.3, 1.2)}[acc_type]
    return max(1_000, int(monthly_cents * rng.uniform(low, high)))


# --- merchants --------------------------------------------------------------------

def _merchants(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Merchants with every category represented at least once."""
    categories = MERCHANT_CATEGORIES + pick(rng, MERCHANT_CATEGORIES, size=n - len(MERCHANT_CATEGORIES),
                                            p=MERCHANT_CATEGORY_P)
    categories = [categories[i] for i in rng.permutation(n)]
    names = _names(rng, n, 2)
    onboarded = rng.integers(to_seconds("2010-01-01") // DAY, to_seconds("2025-12-31") // DAY + 1, size=n) * DAY
    return make_frame("merchants", {
        "merchant_id": [ids.merchant_id(i + 1) for i in range(n)],
        "merchant_name": [f"{name} {CATEGORY_WORD[cat]}" for name, cat in zip(names, categories)],
        "category": categories,
        "risk_tier": ["ELEVATED" if c in ELEVATED_CATEGORIES else "STANDARD" for c in categories],
        "country_code": pick(rng, COUNTRIES, size=n, p=COUNTRY_P),
        "onboarded_date": onboarded.astype(np.int64),
    })


# --- devices and account-device links ---------------------------------------------

def active_intervals(history: pd.DataFrame) -> dict[str, list[tuple[int, int]]]:
    """ACTIVE periods ``[start, end)`` per account from the status history (end may be open: 2**62)."""
    out: dict[str, list[tuple[int, int]]] = {}
    ordered = history.sort_values(["account_id", "event_timestamp", "status_event_id"], kind="mergesort")
    for acc, group in ordered.groupby("account_id", sort=False):
        spans, start = [], None
        for ts, status in zip(group["event_timestamp"].tolist(), group["to_status"].tolist()):
            if status == "ACTIVE" and start is None:
                start = ts
            elif status != "ACTIVE" and start is not None:
                spans.append((start, ts))
                start = None
        if start is not None:
            spans.append((start, 2**62))
        out[acc] = spans
    return out


def is_active_at(spans: list[tuple[int, int]], ts: int) -> bool:
    """True when ``ts`` falls inside one of the ACTIVE ``spans``."""
    return any(start <= ts < end for start, end in spans)


def _device_kind(rng: np.random.Generator, business: bool) -> tuple[str, str]:
    """Draw (device_type, os_family) with a consistent operating system."""
    if business:
        dtype = pick(rng, ["DESKTOP_WEB", "MOBILE_IOS", "MOBILE_ANDROID"], p=[0.7, 0.15, 0.15])
    else:
        dtype = pick(rng, ["MOBILE_IOS", "MOBILE_ANDROID", "TABLET", "DESKTOP_WEB"], p=[0.40, 0.42, 0.06, 0.12])
    return dtype, device_os(rng, dtype)


def device_os(rng: np.random.Generator, device_type: str) -> str:
    """Operating system family consistent with ``device_type``."""
    if device_type == "MOBILE_IOS":
        return "IOS"
    if device_type == "MOBILE_ANDROID":
        return "ANDROID"
    if device_type == "TABLET":
        return pick(rng, ["IOS", "ANDROID"])
    return pick(rng, ["WINDOWS", "MACOS", "LINUX"], p=[0.6, 0.3, 0.1])


def _devices_and_links(customers: pd.DataFrame, accounts: pd.DataFrame, history: pd.DataFrame,
                       rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Customer devices (1-2 historic, ~8% get a new one during the window) linked to their accounts."""
    spans = active_intervals(history)
    open_ts = history[history["reason_code"] == "ACCOUNT_OPENED"].set_index("account_id")["event_timestamp"]
    accounts_of = accounts.groupby("customer_id", sort=False)["account_id"].apply(list)
    candidates = []  # (registered_at, customer order, device order, type, os, [(account, first_seen)])
    for c_idx, cust in enumerate(customers.itertuples(index=False)):
        accs = accounts_of[cust.customer_id]
        first_reg = int(open_ts[accs[0]]) + int(rng.integers(5 * MINUTE, 2 * DAY))
        registrations = [first_reg]
        if rng.random() < 0.30 and first_reg + DAY < WINDOW_START_S:
            registrations.append(int(rng.integers(first_reg + DAY, WINDOW_START_S)))
        if rng.random() < 0.08:
            registrations.append(int(rng.integers(to_seconds("2026-01-15"), to_seconds("2026-09-30"))))
        for d_idx, reg in enumerate(registrations):
            dtype, os_family = _device_kind(rng, cust.customer_type == "BUSINESS")
            links = []
            for acc in accs:
                seen = max(reg, int(open_ts[acc])) + int(rng.integers(MINUTE, HOUR))
                if is_active_at(spans[acc], seen):
                    links.append((acc, seen))
            if links:
                candidates.append((reg, c_idx, d_idx, dtype, os_family, links))
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    device_ids = [ids.device_id(i + 1) for i in range(len(candidates))]
    devices = make_frame("devices", {
        "device_id": device_ids,
        "device_type": [c[3] for c in candidates],
        "os_family": [c[4] for c in candidates],
        "first_registered_at": np.array([c[0] for c in candidates], dtype=np.int64),
    })
    link_rows = [(acc, dev, seen) for dev, c in zip(device_ids, candidates) for acc, seen in c[5]]
    link_rows.sort(key=lambda r: (r[0], r[1]))
    links = make_frame("account_devices", {
        "account_id": [r[0] for r in link_rows],
        "device_id": [r[1] for r in link_rows],
        "first_seen": np.array([r[2] for r in link_rows], dtype=np.int64),
        "last_seen": np.array([r[2] for r in link_rows], dtype=np.int64),
    })
    return devices, links


# --- derived statuses -------------------------------------------------------------

def derive_statuses(tables: dict[str, pd.DataFrame]) -> None:
    """Recompute every derived status / lifecycle date on the master tables in place.

    account_status and close_date come from the latest status event, customer_status
    from the customer's accounts and kyc_status from next_review_date vs. the snapshot.
    """
    history = tables["account_status_history"].sort_values(
        ["account_id", "event_timestamp", "status_event_id"], kind="mergesort")
    last_status = history.groupby("account_id", sort=False)["to_status"].last()
    closed_at = history[history["to_status"] == "CLOSED"].groupby("account_id")["event_timestamp"].last()
    accounts = tables["accounts"]
    accounts["account_status"] = accounts["account_id"].map(last_status)
    accounts["close_date"] = accounts["account_id"].map(closed_at // DAY * DAY).astype("Int64")

    def summarise(statuses: pd.Series) -> str:
        if (statuses == "ACTIVE").any():
            return "ACTIVE"
        return "DORMANT" if (statuses == "DORMANT").any() else "CLOSED"

    customer_status = accounts.groupby("customer_id")["account_status"].agg(summarise)
    customers = tables["customers"]
    customers["customer_status"] = customers["customer_id"].map(customer_status)

    kyc = tables["kyc_profiles"]
    kyc["kyc_status"] = np.where(kyc["next_review_date"] >= day_start(AS_OF_S), "CURRENT", "REVIEW_OVERDUE")
