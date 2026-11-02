"""Stage 5a: planted patterns (suspicious scenarios and benign look-alikes).

Every scenario is produced as ordinary ledger events plus the master-data additions it
needs (a new device and its account link, a reactivation status event). The events are
merged into the baseline stream *before* ledger posting and before any rule runs, so
rules see planted activity exactly like any other activity.

Each scenario is recorded as a :class:`Plant` (pattern, expected label, expected rule,
anchor account, event keys). Plants feed the separate ground-truth file only; no plant
attribute is written to any input table.

Scenario catalogue (counts scale with ``--alerts``, see :func:`plant_counts`):

=========================  ==========  ==================================================
pattern                    label       what is planted
=========================  ==========  ==================================================
VELOCITY_SPIKE             SUSPICIOUS  card testing: 10-14 e-commerce debits at 3-6
                                       merchants within hours (micro then larger amounts)
VELOCITY_SPIKE             BENIGN      11-14 small card payments at one TRANSPORT merchant
NEW_DEVICE_ACTIVITY        SUSPICIOUS  new device, then 1-3 transfers (EUR 3.5-7k) to an
                                       account never paid before (account takeover)
NEW_DEVICE_ACTIVITY        BENIGN      new phone, then a large bill payment to a merchant
                                       the customer already paid weeks earlier
CIRCULAR_TRANSFER          SUSPICIOUS  3-5 accounts of different customers pass ~the same
                                       amount around a ring back to the origin within 48h
CIRCULAR_TRANSFER          BENIGN      own-account sweep around 3 accounts of one customer
DORMANT_REACTIVATION       SUSPICIOUS  online reactivation, large inbound deposit, rapid
                                       transfers out to 2-4 third parties (money mule)
DORMANT_REACTIVATION       BENIGN      in-branch reactivation (identity re-verified),
                                       deposit, planned purchase payment(s)
HIGH_AMOUNT_VS_PROFILE     SUSPICIOUS  purchase of 8-14x the profile maximum at a GIFT_CARDS
                                       / CRYPTO / MONEY_SERVICES / GAMBLING merchant
HIGH_AMOUNT_VS_PROFILE     BENIGN      big-ticket purchase of 3.2-12x the profile maximum,
                                       mostly in planned-purchase categories
=========================  ==========  ==================================================

Every scenario that debits an account first credits it with enough funds, so planted
debits never depend on the baseline balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import ids
from .config import Config
from .master_data import (ELEVATED_CATEGORIES, PLANNED_PURCHASE_CATEGORIES, WINDOW_END_S, WINDOW_START_S,
                          device_os)
from .rng import pick
from .tables import make_frame
from .timeutil import DAY, HOUR, MINUTE
from .transactions import channel_for, closures_in_window, device_at, device_index, make_event

SUSPICIOUS, BENIGN = "SUSPICIOUS", "BENIGN"
PATTERN_RULE = {
    "VELOCITY_SPIKE": "RULE-001",
    "NEW_DEVICE_ACTIVITY": "RULE-002",
    "CIRCULAR_TRANSFER": "RULE-003",
    "DORMANT_REACTIVATION": "RULE-004",
    "HIGH_AMOUNT_VS_PROFILE": "RULE-005",
}
BLOCK_MARGIN = 50 * HOUR  # baseline activity is kept this far away from planted events
CARD_TESTING_CATEGORIES = ["ONLINE_RETAIL", "ELECTRONICS", "GIFT_CARDS", "JEWELRY"]
NEUTRAL_BIG_TICKET_CATEGORIES = ["GENERAL_RETAIL", "JEWELRY"]


@dataclass
class Plant:
    """One planted scenario (feeds the ground truth; never written to an input table)."""

    pattern: str
    label: str
    rule_id: str
    account_id: str
    members: list[str]
    event_keys: list[int]
    trigger_keys: list[int]
    notes: str


@dataclass
class PlantPlan:
    """Everything the planted scenarios add to the dataset."""

    plants: list[Plant] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    devices: list[tuple] = field(default_factory=list)        # (device_id, type, os, registered_at)
    links: list[tuple] = field(default_factory=list)          # (account_id, device_id, first_seen)
    status_events: list[tuple] = field(default_factory=list)  # (account, ts, from, to, reason, role)
    blocked: dict[str, list[tuple[int, int]]] = field(default_factory=dict)


def plant_counts(cfg: Config) -> dict[str, int]:
    """Number of scenarios per kind: suspicious per pattern, benign look-alikes for RULE-001..004,
    and benign big-ticket purchases for RULE-005 (these make up most of the lower-ranked alerts)."""
    return {
        "suspicious_per_pattern": max(2, round(cfg.alerts * 0.04)),
        "lookalike_per_rule": max(1, round(cfg.alerts * 0.025)),
        "big_ticket": round(cfg.alerts * 0.85),
    }


def plan(cfg: Config, tables: dict[str, pd.DataFrame], rng: np.random.Generator) -> PlantPlan:
    """Generate all planted scenarios for ``cfg`` against the master data in ``tables``."""
    planner = _Planner(tables, rng)
    counts = plant_counts(cfg)
    s, f = counts["suspicious_per_pattern"], counts["lookalike_per_rule"]
    for _ in range(f):
        planner.ring(suspicious=False)
    for _ in range(s):
        planner.ring(suspicious=True)
    for suspicious, n in ((True, s), (False, f)):
        for _ in range(n):
            planner.velocity(suspicious)
        for _ in range(n):
            planner.new_device(suspicious)
        for _ in range(n):
            planner.dormant(suspicious)
    for _ in range(s):
        planner.high_amount()
    for _ in range(counts["big_ticket"]):
        planner.big_ticket()
    return planner.finish()


def apply(tables: dict[str, pd.DataFrame], plan_: PlantPlan) -> None:
    """Append the plan's devices, account links and status events to the master tables."""
    devices = make_frame("devices", {
        "device_id": [d[0] for d in plan_.devices], "device_type": [d[1] for d in plan_.devices],
        "os_family": [d[2] for d in plan_.devices],
        "first_registered_at": np.array([d[3] for d in plan_.devices], dtype=np.int64),
    })
    tables["devices"] = pd.concat([tables["devices"], devices], ignore_index=True)
    links = make_frame("account_devices", {
        "account_id": [r[0] for r in plan_.links], "device_id": [r[1] for r in plan_.links],
        "first_seen": np.array([r[2] for r in plan_.links], dtype=np.int64),
        "last_seen": np.array([r[2] for r in plan_.links], dtype=np.int64),
    })
    tables["account_devices"] = (pd.concat([tables["account_devices"], links], ignore_index=True)
                                 .sort_values(["account_id", "device_id"], kind="mergesort").reset_index(drop=True))
    history = tables["account_status_history"]
    events = sorted(plan_.status_events, key=lambda e: (e[1], e[0]))
    start = len(history) + 1
    appended = make_frame("account_status_history", {
        "status_event_id": [ids.status_event_id(start + i) for i in range(len(events))],
        "account_id": [e[0] for e in events],
        "event_timestamp": np.array([e[1] for e in events], dtype=np.int64),
        "from_status": [e[2] for e in events], "to_status": [e[3] for e in events],
        "reason_code": [e[4] for e in events], "changed_by_role": [e[5] for e in events],
    })
    tables["account_status_history"] = pd.concat([history, appended], ignore_index=True)


def _eur(value: float) -> int:
    """EUR -> integer cents."""
    return int(round(value * 100))


class _Planner:
    """Stateful helper that draws scenarios on distinct accounts."""

    def __init__(self, tables: dict[str, pd.DataFrame], rng: np.random.Generator):
        self.rng = rng
        self.plan = PlantPlan()
        self.key = 0
        accounts = tables["accounts"]
        self.owner = dict(zip(accounts["account_id"], accounts["customer_id"]))
        self.acc_type = dict(zip(accounts["account_id"], accounts["account_type"]))
        kyc = tables["kyc_profiles"]
        self.max_amount = dict(zip(kyc["customer_id"], kyc["expected_max_txn_amount"].astype(int)))
        self.devices = device_index(tables)
        self.next_device = len(tables["devices"]) + 1
        self.merchants = {c: g["merchant_id"].tolist() for c, g in tables["merchants"].groupby("category", sort=True)}
        hist = tables["account_status_history"]
        opened = hist[hist["reason_code"] == "ACCOUNT_OPENED"].set_index("account_id")["event_timestamp"]
        closing = closures_in_window(hist)
        stable = accounts[(accounts["account_status"] == "ACTIVE")
                          & (accounts["account_id"].map(opened) < WINDOW_START_S - 30 * DAY)
                          & ~accounts["account_id"].isin(list(closing))]["account_id"].tolist()
        dormant = accounts[accounts["account_status"] == "DORMANT"]["account_id"].tolist()
        self.stable = [stable[i] for i in rng.permutation(len(stable))]
        self.dormant_pool = [dormant[i] for i in rng.permutation(len(dormant))]
        self.used: set[str] = set()

    # -- building blocks -------------------------------------------------------

    def _take(self, pool: list[str], *, savings_ok: bool = False, avoid_customers: tuple = ()) -> str:
        for acc in pool:
            if acc in self.used or self.owner[acc] in avoid_customers:
                continue
            if not savings_ok and self.acc_type[acc] == "SAVINGS":
                continue
            self.used.add(acc)
            return acc
        raise ValueError("not enough eligible accounts for the planted scenarios; increase --accounts")

    def _start(self, earliest_days: int = 20, room_days: int = 3) -> int:
        """Scenario start: ``earliest_days`` into the window, leaving ``room_days`` before it ends."""
        return int(self.rng.integers(WINDOW_START_S + earliest_days * DAY, WINDOW_END_S - room_days * DAY))

    def _merchant(self, categories: list[str]) -> str:
        options = [m for c in categories for m in self.merchants[c]]
        return options[int(self.rng.integers(len(options)))]

    def _event(self, acc: str, ts: int, txn_type: str, amount: int, channel: str, **extra) -> int:
        key = self.key
        self.plan.events.append(make_event(key, acc, ts, txn_type, amount, channel=channel, planted=True, **extra))
        self.key += 1
        return key

    def _fund(self, acc: str, ts: int, needed: int, channel: str = "SYSTEM") -> int:
        """Credit enough money for the scenario's debits (plus a margin)."""
        return self._event(acc, ts, "DEPOSIT", needed + _eur(self.rng.uniform(50, 500)), channel)

    def _digital(self, acc: str, ts: int) -> tuple[str | None, str]:
        """(device, channel) for a customer-initiated payment using an existing device, else branch."""
        device = device_at(self.devices.get(acc, []), ts, self.rng.random())
        return (device[0] if device else None), channel_for("TRANSFER_OUT", device[1] if device else None, 0.0)

    def _transfer(self, src: str, dst: str, ts: int, amount: int, device: str | None, channel: str) -> tuple[int, int]:
        debit = self._event(src, ts, "TRANSFER_OUT", amount, channel, device=device, pair=self.key)
        credit = self._event(dst, ts + int(self.rng.integers(1, 91)), "TRANSFER_IN", amount,
                             channel_for("TRANSFER_IN", None, 0.0), pair=debit)
        return debit, credit

    def _new_device(self, acc: str, ts: int, device_type: str) -> tuple[str, int]:
        """Register a new device at ``ts`` and link it to ``acc`` a few minutes later."""
        dev = ids.device_id(self.next_device)
        self.next_device += 1
        seen = ts + int(self.rng.integers(MINUTE, 5 * MINUTE))
        self.plan.devices.append((dev, device_type, device_os(self.rng, device_type), ts))
        self.plan.links.append((acc, dev, seen))
        return dev, seen

    def _split(self, total: int, parts: int) -> list[int]:
        weights = self.rng.uniform(0.6, 1.4, parts)
        amounts = [int(total * w / weights.sum()) for w in weights]
        amounts[-1] += total - sum(amounts)
        return amounts

    def _record(self, pattern: str, label: str, account: str, members: list[str], first_key: int,
                trigger_keys: list[int], notes: str) -> None:
        self.plan.plants.append(Plant(pattern, label, PATTERN_RULE[pattern], account, members,
                                      list(range(first_key, self.key)), trigger_keys, notes))

    # -- scenarios -------------------------------------------------------------

    def velocity(self, suspicious: bool) -> None:
        acc, first_key, rng = self._take(self.stable), self.key, self.rng
        t0 = self._start()
        if suspicious:
            n, tests = int(rng.integers(10, 15)), int(rng.integers(3, 5))
            pool = [m for c in CARD_TESTING_CATEGORIES for m in self.merchants[c]]
            chosen = [pool[i] for i in rng.choice(len(pool), size=min(len(pool), int(rng.integers(3, 7))),
                                                  replace=False)]
            cap = int(self.max_amount[self.owner[acc]] * 2.4)
            amounts = [int(rng.integers(100, 1000)) if i < tests else min(cap, int(rng.integers(15_000, 90_000)))
                       for i in range(n)]
            merchants = [chosen[i % len(chosen)] for i in range(n)]
            gaps, channel = rng.integers(2 * MINUTE, 30 * MINUTE, n), "ECOMMERCE"
            notes = f"card testing: {n} e-commerce debits at {len(chosen)} merchants, {tests} micro test payments"
        else:
            n = int(rng.integers(11, 15))
            merchant = self._merchant(["TRANSPORT"])
            amounts, merchants = [int(rng.integers(150, 1200)) for _ in range(n)], [merchant] * n
            gaps, channel = rng.integers(20 * MINUTE, 90 * MINUTE, n), "CARD_POS"
            notes = f"benign: {n} small card payments at one TRANSPORT merchant ({merchant}) in one day"
        self._fund(acc, t0 - int(rng.integers(HOUR, 4 * HOUR)), sum(amounts))
        times = t0 + np.cumsum(gaps) - gaps[0]
        triggers = [self._event(acc, int(ts), "PURCHASE", amt, channel, merchant=m)
                    for ts, amt, m in zip(times, amounts, merchants)]
        self._record("VELOCITY_SPIKE", SUSPICIOUS if suspicious else BENIGN, acc, [acc], first_key, triggers, notes)

    def new_device(self, suspicious: bool) -> None:
        acc, first_key, rng = self._take(self.stable), self.key, self.rng
        if suspicious:
            t0 = self._start()
            mule = self._take(self.stable, avoid_customers=(self.owner[acc],))
            device_type = pick(rng, ["DESKTOP_WEB", "MOBILE_ANDROID"])
            dev, seen = self._new_device(acc, t0, device_type)
            amounts = self._split(_eur(rng.uniform(3500, 7000)), int(rng.integers(1, 4)))
            self._fund(acc, t0 - int(rng.integers(2 * HOUR, 10 * HOUR)), sum(amounts))
            channel = channel_for("TRANSFER_OUT", device_type, 0.0)
            times = sorted(seen + int(x) for x in rng.integers(10 * MINUTE, 6 * HOUR, len(amounts)))
            triggers = [self._transfer(acc, mule, ts, amt, dev, channel)[0] for ts, amt in zip(times, amounts)]
            members = [acc, mule]
            notes = f"account takeover: new device {dev}, {len(amounts)} transfer(s) to never-paid account {mule}"
        else:
            t0 = self._start(earliest_days=70)
            merchant = self._merchant([c for c in PLANNED_PURCHASE_CATEGORIES if c != "ELECTRONICS"])
            prior_ts = t0 - int(rng.integers(20 * DAY, 60 * DAY))
            prior = _eur(rng.uniform(100, 600))
            self._fund(acc, prior_ts - int(rng.integers(HOUR, 3 * HOUR)), prior)
            self._event(acc, prior_ts, "BILL_PAYMENT", prior, "BRANCH", merchant=merchant)
            dev, seen = self._new_device(acc, t0, "MOBILE_IOS")
            amount = _eur(rng.uniform(3500, 6000))
            self._fund(acc, t0 - int(rng.integers(HOUR, 3 * HOUR)), amount)
            pay_ts = seen + int(rng.integers(30 * MINUTE, 12 * HOUR))
            triggers = [self._event(acc, pay_ts, "BILL_PAYMENT", amount, "MOBILE_APP", merchant=merchant, device=dev)]
            members = [acc]
            notes = f"benign: new phone {dev}, then a large payment to {merchant}, already paid weeks before"
        self._record("NEW_DEVICE_ACTIVITY", SUSPICIOUS if suspicious else BENIGN, acc, members, first_key,
                     triggers, notes)

    def ring(self, suspicious: bool) -> None:
        rng, first_key = self.rng, self.key
        if suspicious:
            members: list[str] = []
            for _ in range(int(rng.integers(3, 6))):
                members.append(self._take(self.stable, avoid_customers=tuple(self.owner[m] for m in members)))
            first = _eur(rng.uniform(4000, 9500))
            amounts = [first]
            for _ in members[1:]:
                amounts.append(int(amounts[-1] * rng.uniform(0.98, 1.0)))
            gaps = rng.integers(HOUR, 12 * HOUR, len(members))
            notes = f"ring of {len(members)} accounts of {len(members)} customers, first hop EUR {first / 100:.2f}"
        else:
            members = self._own_accounts(3)
            first = _eur(rng.uniform(5000, 9000))
            amounts = [first] * len(members)
            gaps = rng.integers(2 * HOUR, 20 * HOUR, len(members))
            notes = f"benign own-account sweep around 3 accounts of {self.owner[members[0]]}, EUR {first / 100:.2f}"
        t0 = self._start()
        self._fund(members[0], t0 - int(rng.integers(HOUR, 6 * HOUR)), first)
        triggers = []
        for i, (ts, amount) in enumerate(zip(t0 + np.cumsum(gaps) - gaps[0], amounts)):
            src, dst = members[i], members[(i + 1) % len(members)]
            device, channel = self._digital(src, int(ts))
            triggers.extend(self._transfer(src, dst, int(ts), amount, device, channel))
        self._record("CIRCULAR_TRANSFER", SUSPICIOUS if suspicious else BENIGN, members[0], members, first_key,
                     triggers, notes)

    def _own_accounts(self, n: int) -> list[str]:
        """``n`` unused stable accounts (any product) of one customer."""
        by_customer: dict[str, list[str]] = {}
        for acc in self.stable:
            if acc not in self.used:
                by_customer.setdefault(self.owner[acc], []).append(acc)
        for customer in sorted(c for c, accs in by_customer.items() if len(accs) >= n):
            chosen = sorted(by_customer[customer])[:n]
            self.used.update(chosen)
            return chosen
        raise ValueError("no customer with enough accounts for a benign own-account ring; increase --accounts")

    def dormant(self, suspicious: bool) -> None:
        rng, first_key = self.rng, self.key
        acc = self._take(self.dormant_pool, savings_ok=True)
        t0 = self._start(room_days=7)
        if suspicious:
            reason, role, deposit_channel = "REACTIVATED_ONLINE", "CUSTOMER", "SYSTEM"
            funds = _eur(rng.uniform(13000, 20000))
        else:
            reason, role, deposit_channel = "REACTIVATED_IN_BRANCH", "OPERATIONS_ANALYST", "BRANCH"
            funds = _eur(rng.uniform(13000, 18000))
        self.plan.status_events.append((acc, t0, "DORMANT", "ACTIVE", reason, role))
        self._event(acc, t0 + int(rng.integers(10 * MINUTE, 2 * HOUR)), "DEPOSIT", funds, deposit_channel)
        if suspicious:
            amounts = self._split(int(funds * rng.uniform(0.90, 0.98)), int(rng.integers(2, 5)))
            mules = []
            for _ in amounts:
                mules.append(self._take(self.stable, avoid_customers=(self.owner[acc], *map(self.owner.get, mules))))
            times = sorted(t0 + int(x) for x in rng.integers(3 * HOUR, 5 * DAY, len(amounts)))
            triggers = []
            for ts, amount, mule in zip(times, amounts, mules):
                device, channel = self._digital(acc, ts)
                triggers.append(self._transfer(acc, mule, ts, amount, device, channel)[0])
            members = [acc, *mules]
            notes = f"money mule: online reactivation, EUR {funds / 100:.2f} in, {len(amounts)} transfers out"
        else:
            amounts = self._split(int(funds * rng.uniform(0.85, 0.95)), int(rng.integers(1, 3)))
            merchant = self._merchant(["FURNITURE", "HOME_IMPROVEMENT", "AUTOMOTIVE", "TRAVEL"])
            times = sorted(t0 + int(x) for x in rng.integers(DAY, 6 * DAY, len(amounts)))
            triggers = [self._event(acc, ts, "BILL_PAYMENT", amount, "BRANCH", merchant=merchant)
                        for ts, amount in zip(times, amounts)]
            members = [acc]
            notes = f"benign: in-branch reactivation with identity check, planned payment(s) to {merchant}"
        self._record("DORMANT_REACTIVATION", SUSPICIOUS if suspicious else BENIGN, acc, members, first_key,
                     triggers, notes)

    def high_amount(self) -> None:
        rng, first_key = self.rng, self.key
        acc = self._take(self.stable)
        t0 = self._start()
        ratio = rng.uniform(8, 14)
        amount = int(self.max_amount[self.owner[acc]] * ratio)
        merchant = self._merchant(ELEVATED_CATEGORIES)
        self._fund(acc, t0 - int(rng.integers(30 * MINUTE, 6 * HOUR)), amount)
        trigger = self._event(acc, t0, "PURCHASE", amount, "ECOMMERCE", merchant=merchant)
        self._record("HIGH_AMOUNT_VS_PROFILE", SUSPICIOUS, acc, [acc], first_key, [trigger],
                     f"purchase of {ratio:.1f}x the profile maximum at high-risk merchant {merchant}")

    def big_ticket(self) -> None:
        rng, first_key = self.rng, self.key
        acc = self._take(self.stable)
        t0 = self._start()
        ratio = rng.uniform(3.2, 12)
        amount = int(self.max_amount[self.owner[acc]] * ratio)
        planned = rng.random() < 0.85
        category = pick(rng, PLANNED_PURCHASE_CATEGORIES if planned else NEUTRAL_BIG_TICKET_CATEGORIES)
        merchant = self._merchant([category])
        self._fund(acc, t0 - int(rng.integers(30 * MINUTE, 6 * HOUR)), amount)
        if category == "INSURANCE":
            trigger = self._event(acc, t0, "BILL_PAYMENT", amount, "BRANCH", merchant=merchant)
        else:
            trigger = self._event(acc, t0, "PURCHASE", amount, pick(rng, ["CARD_POS", "ECOMMERCE"]),
                                  merchant=merchant)
        self._record("HIGH_AMOUNT_VS_PROFILE", BENIGN, acc, [acc], first_key, [trigger],
                     f"benign big-ticket {category} purchase of {ratio:.1f}x the profile maximum")

    def finish(self) -> PlantPlan:
        """Block baseline activity around every planted event and return the plan."""
        for ev in self.plan.events:
            self.plan.blocked.setdefault(ev["account_id"], []).append((ev["ts"] - BLOCK_MARGIN, ev["ts"] + BLOCK_MARGIN))
        for acc, ts, *_ in self.plan.status_events:
            self.plan.blocked.setdefault(acc, []).append((ts - BLOCK_MARGIN, ts + 8 * DAY))
        return self.plan
