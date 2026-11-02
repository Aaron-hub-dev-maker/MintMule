# Data dictionary

Synthetic fraud / AML analyst-review prioritisation dataset. **Every record is synthetic.**
No real personal data and no external data sources are used; names are built from a fixed
syllable list and all values come from one seeded random generator.

The authoritative machine-readable contract is [`generator/schema.sql`](../generator/schema.sql);
the ER diagram is in [`er_diagram.md`](er_diagram.md). A test (`tests/test_stage2_schema.py`)
checks that every column of every table below matches the schema (name, type, required flag).

## Conventions

| Topic | Convention |
|---|---|
| Timestamps | UTC, `YYYY-MM-DDTHH:MM:SS`, second resolution |
| Dates | `YYYY-MM-DD` |
| Money | EUR, 2 decimals, stored as SQLite `REAL`; computed internally in integer cents |
| Identifiers | zero-padded, immutable once assigned, identical in every table that references them |
| Calendar | history from 2016-01-01; transaction window 2026-01-01T00:00:00 – 2026-10-15T23:59:59; snapshot (as-of) 2026-10-22T00:00:00 |
| Derived values | balances, totals, counts, scores, ranks, statuses and lifecycle dates are computed from source records, never drawn at random |
| Event tables | `account_status_history` and `analyst_actions` are append-only (SQLite triggers reject UPDATE/DELETE) |
| Nulls | empty field in CSV, `null` in JSON, `NULL` in SQL |

### Actors

| Actor | Where it appears |
|---|---|
| Customer / account holder | `customers`, `accounts`; `changed_by_role = CUSTOMER` for self-service reactivation |
| Operations analyst | `analyst_actions.actor_role = OPERATIONS_ANALYST` (`OPS-01`..`OPS-08`): reviews, false-positive and escalation dispositions; branch reactivations |
| Risk / compliance reviewer | `analyst_actions.actor_role = RISK_REVIEWER` (`RISK-01`..`RISK-03`): confirms suspicious activity (four-eyes); approves rules |
| System administrator | `risk_rules.owner_role = SYSTEM_ADMINISTRATOR`: owns rule configuration and versions |
| System | automatic case assignment, inactivity status changes, fees and interest |

### Identifier formats

| Entity | Format | Example |
|---|---|---|
| Customer | `CUS-NNNNNN` | CUS-000001 |
| KYC profile | `KYC-NNNNNN` (same number as its customer) | KYC-000001 |
| Account | `ACC-<customer_id>-NN` (NN = account sequence of that customer) | ACC-CUS-000001-01 |
| Merchant | `MER-NNNNNN` | MER-000001 |
| Device | `DEV-NNNNNN` | DEV-000001 |
| Transaction | `TXN-2026-NNNNNNNN` (posting order) | TXN-2026-00000001 |
| Transfer link | `TRF-2026-NNNNNN` | TRF-2026-000001 |
| Risk rule | `RULE-NNN` | RULE-001 |
| Alert | `ALT-2026-NNNNNN` (creation order) | ALT-2026-000001 |
| Case | `CASE-2026-NNNNNN` (opening order) | CASE-2026-000001 |
| Analyst action | `ACT-2026-NNNNNN` | ACT-2026-000001 |
| Account status event | `ASH-NNNNNNNN` (append order) | ASH-00000001 |

---

## customers

Customer / account holder master record.

- **Primary key:** `customer_id`. **Foreign keys:** none.
- **Cardinality:** 1 customer → exactly 1 KYC profile; 1 customer → 1..99 accounts; 0..n alerts; 0..n cases.
- **Optional relationships:** alerts and cases (most customers have none).
- **Effective dates:** `onboarding_date` (start of relationship; equals the open date of account `-01`).
- **Lifecycle:** `customer_status` is derived from the customer's accounts at the snapshot date: `ACTIVE` if any account is ACTIVE, else `DORMANT` if any is DORMANT, else `CLOSED`.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| customer_id | TEXT | yes | `CUS-NNNNNN` | – | yes (PK) | GLOB format; immutable | Customer identifier |
| customer_type | TEXT | yes | INDIVIDUAL, BUSINESS | – | no | enum | Legal form of the account holder |
| display_name | TEXT | yes | synthetic syllable name | – | no | non-empty | Pseudonymous synthetic name (not a real person) |
| birth_year | INTEGER | no | 1940–2006 | year | no | set for INDIVIDUAL, null for BUSINESS | Year of birth |
| country_code | TEXT | yes | AT, BE, DE, ES, FR, IE, IT, LU, NL, PT | – | no | enum | Country of residence / registration |
| segment | TEXT | yes | RETAIL, AFFLUENT, SMALL_BUSINESS, CORPORATE | – | no | RETAIL/AFFLUENT only for INDIVIDUAL; SMALL_BUSINESS/CORPORATE only for BUSINESS | Commercial segment |
| onboarding_date | TEXT | yes | `YYYY-MM-DD`, 2016-01-01..2026-06-30 | date | no | ≤ every account `open_date` of the customer | Relationship start date |
| customer_status | TEXT | yes | ACTIVE, DORMANT, CLOSED | – | no | derived from account statuses (see lifecycle) | Derived lifecycle state |

## kyc_profiles

Know-your-customer profile used by the "high amount vs. profile" rule and the disposition decision rule.

- **Primary key:** `kyc_id`. **Foreign keys:** `customer_id` → `customers` (UNIQUE: 1:1).
- **Cardinality:** exactly one profile per customer, mandatory on both sides.
- **Effective dates:** `verified_date` (profile effective from) and `next_review_date` (periodic review due).
- **Lifecycle:** `kyc_status` derived: `CURRENT` if `next_review_date` ≥ snapshot date, else `REVIEW_OVERDUE`.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| kyc_id | TEXT | yes | `KYC-NNNNNN` | – | yes (PK) | number equals the customer number | KYC profile identifier |
| customer_id | TEXT | yes | `CUS-NNNNNN` | – | yes | FK → customers | Profiled customer |
| risk_rating | TEXT | yes | LOW, MEDIUM, HIGH | – | no | enum | Customer risk rating |
| pep_flag | INTEGER | yes | 0, 1 | flag | no | enum | Politically exposed person indicator (synthetic) |
| occupation_or_industry | TEXT | yes | EMPLOYED, SELF_EMPLOYED, RETIRED, STUDENT, UNEMPLOYED (individuals); RETAIL_TRADE, PROFESSIONAL_SERVICES, HOSPITALITY, CONSTRUCTION, IT_SERVICES, WHOLESALE (businesses) | – | no | value set must match customer_type | Occupation (individual) or industry (business) |
| expected_monthly_volume | REAL | yes | > 0 | EUR | no | positive | Declared expected monthly turnover |
| expected_max_txn_amount | REAL | yes | > 0 | EUR | no | positive; < expected_monthly_volume | Declared largest expected single transaction |
| verified_date | TEXT | yes | `YYYY-MM-DD` | date | no | ≥ onboarding_date | Date the profile was last verified |
| next_review_date | TEXT | yes | `YYYY-MM-DD` | date | no | = verified_date + 365 / 730 / 1095 days for HIGH / MEDIUM / LOW | Next periodic review |
| kyc_status | TEXT | yes | CURRENT, REVIEW_OVERDUE | – | no | derived from next_review_date vs. snapshot | Derived review status |

## accounts

Deposit account owned by one customer.

- **Primary key:** `account_id`. **Foreign keys:** `customer_id` → `customers`. **Alternate key:** (`customer_id`, `account_seq`).
- **Cardinality:** n accounts → 1 customer; 1 account → 0..n transactions, 0..n device links, 1..n status events, 0..n alerts.
- **Optional relationships:** `overdraft_limit` (only some CURRENT / BUSINESS_CURRENT accounts), `close_date` (only closed accounts).
- **Effective dates:** `open_date` (date of the ACCOUNT_OPENED status event), `close_date` (date of the CLOSED status event).
- **Lifecycle:** `ACTIVE` ⇄ `DORMANT` → `CLOSED` (CLOSED is terminal); `account_status` is the `to_status` of the latest `account_status_history` event.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| account_id | TEXT | yes | `ACC-CUS-NNNNNN-NN` | – | yes (PK) | embeds `customer_id` and `account_seq` | Account identifier |
| customer_id | TEXT | yes | `CUS-NNNNNN` | – | no | FK → customers | Owning customer |
| account_seq | INTEGER | yes | 1–99 | sequence | with customer_id | consecutive from 1 per customer | Account sequence for the customer (the `-NN` suffix) |
| account_type | TEXT | yes | CURRENT, SAVINGS, BUSINESS_CURRENT | – | no | seq 1 is CURRENT (individual) or BUSINESS_CURRENT (business); BUSINESS_CURRENT only for businesses | Product type |
| currency | TEXT | yes | EUR | – | no | enum | Account currency |
| open_date | TEXT | yes | `YYYY-MM-DD` | date | no | ≥ customer onboarding_date; = date of the ACCOUNT_OPENED event | Opening date |
| close_date | TEXT | no | `YYYY-MM-DD` | date | no | set iff account_status = CLOSED; ≥ open_date; = date of the CLOSED event | Closing date |
| overdraft_limit | REAL | no | > 0 | EUR | no | only CURRENT / BUSINESS_CURRENT | Approved overdraft; balance may go down to −overdraft_limit |
| opening_balance | REAL | yes | ≥ 0 | EUR | no | balance at max(open, 2026-01-01T00:00:00); 0 for accounts opened in the window or closed before it | Starting ledger balance for the observation window |
| account_status | TEXT | yes | ACTIVE, DORMANT, CLOSED | – | no | derived from the latest status event | Current lifecycle state |

## merchants

Card / bill-payment acceptors.

- **Primary key:** `merchant_id`. **Foreign keys:** none.
- **Cardinality:** 1 merchant → 0..n transactions.
- **Effective dates:** `onboarded_date`. **Lifecycle:** none (all merchants are live during the window).

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| merchant_id | TEXT | yes | `MER-NNNNNN` | – | yes (PK) | GLOB format | Merchant identifier |
| merchant_name | TEXT | yes | synthetic name | – | no | non-empty | Synthetic trading name |
| category | TEXT | yes | GROCERY, RESTAURANT, TRANSPORT, FUEL, UTILITIES, TELECOM, ONLINE_RETAIL, GENERAL_RETAIL, ELECTRONICS, TRAVEL, FURNITURE, HOME_IMPROVEMENT, AUTOMOTIVE, INSURANCE, HEALTH, ENTERTAINMENT, JEWELRY, GIFT_CARDS, CRYPTO_EXCHANGE, MONEY_SERVICES, GAMBLING | – | no | enum; every category has ≥ 1 merchant | Merchant category |
| risk_tier | TEXT | yes | STANDARD, ELEVATED | – | no | derived: ELEVATED iff category in GIFT_CARDS, CRYPTO_EXCHANGE, MONEY_SERVICES, GAMBLING | Category risk tier |
| country_code | TEXT | yes | AT, BE, DE, ES, FR, IE, IT, LU, NL, PT | – | no | enum | Country of the acceptor |
| onboarded_date | TEXT | yes | `YYYY-MM-DD`, before 2026-01-01 | date | no | ≤ date of every transaction at the merchant | Acquirer onboarding date |

## devices

Digital-banking devices (phones, tablets, browsers).

- **Primary key:** `device_id`. **Foreign keys:** none.
- **Cardinality:** 1 device → 1..n account links (via `account_devices`), 0..n transactions.
- **Effective dates:** `first_registered_at`. **Lifecycle:** none.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| device_id | TEXT | yes | `DEV-NNNNNN` | – | yes (PK) | GLOB format | Device identifier |
| device_type | TEXT | yes | MOBILE_IOS, MOBILE_ANDROID, TABLET, DESKTOP_WEB | – | no | enum | Device class |
| os_family | TEXT | yes | IOS, ANDROID, WINDOWS, MACOS, LINUX | – | no | MOBILE_IOS→IOS; MOBILE_ANDROID→ANDROID; TABLET→IOS/ANDROID; DESKTOP_WEB→WINDOWS/MACOS/LINUX | Operating system family |
| first_registered_at | TEXT | yes | `YYYY-MM-DDTHH:MM:SS` | timestamp | no | ≤ first_seen of each of its account links | First time the bank saw the device |

## account_devices

Bridge table: N:M between accounts and devices.

- **Primary key:** (`account_id`, `device_id`). **Foreign keys:** `account_id` → `accounts`, `device_id` → `devices`.
- **Cardinality:** an account has 0..n devices; a device serves 1..n accounts.
- **Effective dates:** `first_seen` (link start), `last_seen` (latest observed use).
- **Lifecycle:** none; `last_seen` is derived as max(`first_seen`, latest transaction using the device on the account).

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| account_id | TEXT | yes | `ACC-CUS-NNNNNN-NN` | – | with device_id (PK) | FK → accounts | Linked account |
| device_id | TEXT | yes | `DEV-NNNNNN` | – | with account_id (PK) | FK → devices | Linked device |
| first_seen | TEXT | yes | `YYYY-MM-DDTHH:MM:SS` | timestamp | no | ≥ device first_registered_at; ≥ account opening event; ≤ account closing event | First time the device was used on the account |
| last_seen | TEXT | yes | `YYYY-MM-DDTHH:MM:SS` | timestamp | no | ≥ first_seen; derived from transactions | Last observed use |

## account_status_history

Append-only event table of account lifecycle changes.

- **Primary key:** `status_event_id`. **Foreign keys:** `account_id` → `accounts`.
- **Cardinality:** 1 account → 1..n events (the first is always ACCOUNT_OPENED).
- **Effective dates:** `event_timestamp` (the new status is effective from this instant).
- **Lifecycle:** allowed transitions: (none)→ACTIVE (ACCOUNT_OPENED), ACTIVE→DORMANT (INACTIVITY_180_DAYS), DORMANT→ACTIVE (REACTIVATED_ONLINE / REACTIVATED_IN_BRANCH), ACTIVE→CLOSED and DORMANT→CLOSED (CUSTOMER_REQUEST). `from_status` must equal the previous event's `to_status`.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| status_event_id | TEXT | yes | `ASH-NNNNNNNN` | – | yes (PK) | numbered in append order | Event identifier |
| account_id | TEXT | yes | `ACC-CUS-NNNNNN-NN` | – | no | FK → accounts | Account whose status changed |
| event_timestamp | TEXT | yes | `YYYY-MM-DDTHH:MM:SS` | timestamp | no | strictly increasing per account | When the change took effect |
| from_status | TEXT | no | ACTIVE, DORMANT, CLOSED | – | no | null only on the first (ACCOUNT_OPENED) event; = previous to_status | Status before the change |
| to_status | TEXT | yes | ACTIVE, DORMANT, CLOSED | – | no | ≠ from_status; allowed transition | Status after the change |
| reason_code | TEXT | yes | ACCOUNT_OPENED, INACTIVITY_180_DAYS, REACTIVATED_ONLINE, REACTIVATED_IN_BRANCH, CUSTOMER_REQUEST | – | no | must match the transition (see lifecycle) | Why the status changed |
| changed_by_role | TEXT | yes | SYSTEM, CUSTOMER, OPERATIONS_ANALYST | – | no | SYSTEM for opening/inactivity, CUSTOMER for online reactivation, OPERATIONS_ANALYST for branch reactivation and closure | Actor that made the change |

## risk_rules

Transparent, versioned rule configuration owned by the system administrator. Scoring formula
(same for every rule): `score = weight + (score_ceiling − weight) × (1 − 0.5 ^ ((metric − threshold) / (midpoint − threshold)))`,
rounded to 2 decimals: `weight` at the threshold, halfway to `score_ceiling` at the midpoint, rising with the
metric without ever saturating. A rule fires only when `metric ≥ threshold`. Full rule logic: [`risk_rules.md`](risk_rules.md).

- **Primary key:** `rule_id`. **Alternate key:** `rule_code`. **Foreign keys:** none.
- **Cardinality:** 1 rule → 0..n alerts.
- **Effective dates:** `effective_from` / `effective_to` (null = open-ended). **Lifecycle:** `is_active` 1 = evaluated, 0 = retired (kept for traceability).

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| rule_id | TEXT | yes | `RULE-NNN` | – | yes (PK) | GLOB format | Rule identifier |
| rule_code | TEXT | yes | upper snake case | – | yes | unique | Stable mnemonic, e.g. VELOCITY_SPIKE |
| rule_name | TEXT | yes | free text | – | no | non-empty | Human-readable name |
| description | TEXT | yes | free text | – | no | non-empty | Plain-language rule logic |
| applies_to | TEXT | yes | free text | – | no | non-empty | Transactions / events the rule evaluates |
| metric_name | TEXT | yes | free text | – | no | non-empty | Measured quantity |
| metric_unit | TEXT | yes | COUNT, EUR, RATIO | – | no | enum | Unit of metric, threshold and midpoint |
| lookback_hours | INTEGER | yes | ≥ 0 | hours | no | 0 = single-transaction rule | Evaluation window length |
| threshold_value | REAL | yes | > 0 | metric_unit | no | < midpoint_value | Minimum metric that fires the rule |
| midpoint_value | REAL | yes | > threshold | metric_unit | no | > threshold_value | Metric at which the score is halfway between `weight` and `score_ceiling` |
| weight | REAL | yes | 0–100 | points | no | range | Score when the metric just meets the threshold |
| score_ceiling | REAL | yes | > weight, ≤ 100 | points | no | weight < ceiling ≤ 100 | Score the rule approaches but never reaches: 100 for the multi-event pattern rules, 92 for the single-transaction RULE-005 |
| rule_version | INTEGER | yes | ≥ 1 | version | no | positive | Configuration version |
| effective_from | TEXT | yes | `YYYY-MM-DD` | date | no | ≤ effective_to | First day the rule applies |
| effective_to | TEXT | no | `YYYY-MM-DD` | date | no | null = still effective | Last day the rule applies |
| is_active | INTEGER | yes | 0, 1 | flag | no | inactive rules produce no alerts | Whether the rule is evaluated |
| owner_role | TEXT | yes | SYSTEM_ADMINISTRATOR | – | no | enum | Role that maintains the configuration |
| approved_by_role | TEXT | yes | RISK_REVIEWER | – | no | enum | Role that approved the configuration |

## transactions

Ledger postings (one row per debit or credit) in chronological order per account.

- **Primary key:** `transaction_id`. **Foreign keys:** `account_id` → `accounts`; `merchant_id` → `merchants` (optional); `device_id` → `devices` (optional); (`account_id`, `device_id`) → `account_devices` (optional).
- **Cardinality:** n transactions → 1 account; 0..1 merchant; 0..1 device; a transaction is a leg of 0..1 transfer link and a trigger of 0..n alerts.
- **Optional relationships:** `merchant_id` iff `txn_type` ∈ PURCHASE, BILL_PAYMENT, REFUND; `device_id` iff `channel` ∈ MOBILE_APP, ONLINE_BANKING.
- **Effective dates:** `txn_timestamp`; must fall inside an ACTIVE interval of the account (after opening, before closing, not while DORMANT).
- **Lifecycle:** none (postings are immutable). Balances: ordered by (`txn_timestamp`, `transaction_id`) per account, the first `balance_before` equals `accounts.opening_balance` and every later `balance_before` equals the previous `balance_after`.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| transaction_id | TEXT | yes | `TXN-2026-NNNNNNNN` | – | yes (PK) | numbered in posting order | Transaction identifier |
| account_id | TEXT | yes | `ACC-CUS-NNNNNN-NN` | – | no | FK → accounts; account ACTIVE at txn_timestamp | Posting account |
| txn_timestamp | TEXT | yes | `YYYY-MM-DDTHH:MM:SS` | timestamp | no | strictly increasing per account | Booking time |
| direction | TEXT | yes | DEBIT, CREDIT | – | no | consistent with txn_type | Money out (DEBIT) or in (CREDIT) |
| txn_type | TEXT | yes | DEBIT: PURCHASE, ATM_WITHDRAWAL, BILL_PAYMENT, FEE, TRANSFER_OUT, ACCOUNT_CLOSURE; CREDIT: SALARY, DEPOSIT, REFUND, INTEREST, TRANSFER_IN | – | no | enum; TRANSFER_* rows are legs of exactly one transfer link | Business type |
| channel | TEXT | yes | CARD_POS, ECOMMERCE, ATM, MOBILE_APP, ONLINE_BANKING, BRANCH, SYSTEM | – | no | PURCHASE/REFUND: CARD_POS, ECOMMERCE; ATM_WITHDRAWAL: ATM; FEE/INTEREST/SALARY/TRANSFER_IN: SYSTEM (credit legs are booked by the bank); DEPOSIT: SYSTEM, BRANCH; BILL_PAYMENT/TRANSFER_OUT: MOBILE_APP, ONLINE_BANKING, BRANCH; ACCOUNT_CLOSURE: BRANCH | Initiation channel |
| amount | REAL | yes | > 0 | EUR | no | positive, 2 decimals | Absolute amount |
| currency | TEXT | yes | EUR | – | no | enum | Currency |
| balance_before | REAL | yes | any | EUR | no | = previous balance_after (or opening_balance) | Ledger balance before posting |
| balance_after | REAL | yes | ≥ −overdraft_limit (≥ 0 without overdraft) | EUR | no | = balance_before + amount (CREDIT) or − amount (DEBIT) | Ledger balance after posting |
| merchant_id | TEXT | no | `MER-NNNNNN` | – | no | FK → merchants; see optional relationships | Merchant (card / bill payments, refunds) |
| device_id | TEXT | no | `DEV-NNNNNN` | – | no | FK → devices and → account_devices; first_seen ≤ txn_timestamp ≤ last_seen | Device used (digital channels only) |

## transfer_links

Pairs the two legs of an account-to-account transfer.

- **Primary key:** `transfer_id`. **Foreign keys:** `source_txn_id`, `dest_txn_id` → `transactions` (each UNIQUE: a transaction is a leg of at most one link); `source_account_id`, `dest_account_id` → `accounts`.
- **Cardinality:** 1 link → exactly 1 debit leg and exactly 1 credit leg.
- **Effective dates:** `initiated_at` (= timestamp of the debit leg). **Lifecycle:** none.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| transfer_id | TEXT | yes | `TRF-2026-NNNNNN` | – | yes (PK) | numbered in posting order of the debit leg | Transfer identifier |
| source_txn_id | TEXT | yes | `TXN-2026-NNNNNNNN` | – | yes | FK → transactions; DEBIT / TRANSFER_OUT on source_account_id | Debit leg |
| dest_txn_id | TEXT | yes | `TXN-2026-NNNNNNNN` | – | yes | FK → transactions; CREDIT / TRANSFER_IN on dest_account_id; timestamp ≥ debit leg | Credit leg |
| source_account_id | TEXT | yes | `ACC-CUS-NNNNNN-NN` | – | no | FK → accounts; = account of the debit leg | Paying account |
| dest_account_id | TEXT | yes | `ACC-CUS-NNNNNN-NN` | – | no | FK → accounts; ≠ source_account_id; = account of the credit leg | Receiving account |
| amount | REAL | yes | > 0 | EUR | no | = debit leg amount = credit leg amount | Transferred amount |
| initiated_at | TEXT | yes | `YYYY-MM-DDTHH:MM:SS` | timestamp | no | = debit leg timestamp | Initiation time |
| transfer_scope | TEXT | yes | OWN_ACCOUNTS, THIRD_PARTY | – | no | derived: OWN_ACCOUNTS iff both accounts belong to the same customer | Relationship between the parties |

## alerts

Rule hits retained for analyst review: the top-N candidates by score (N = `--alerts`).

- **Primary key:** `alert_id`. **Alternate key:** `priority_rank`. **Foreign keys:** `rule_id` → `risk_rules`, `account_id` → `accounts`, `customer_id` → `customers`.
- **Cardinality:** n alerts → 1 rule, 1 account, 1 customer; 1 alert → 1..n triggering transactions (`alert_transactions`); 1 alert → 1 case (`case_alerts`; the bridge allows N:M).
- **Effective dates:** `created_at` (hourly detection batch after the last triggering transaction).
- **Lifecycle:** `alert_status` derived from analyst actions: OPEN (no review) → IN_REVIEW (reviewed) → CONFIRMED / FALSE_POSITIVE / ESCALATED (disposition).
- **Ranking:** `priority_rank` 1..N ordered by `score` DESC, then `created_at` ASC, then `alert_id` ASC.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| alert_id | TEXT | yes | `ALT-2026-NNNNNN` | – | yes (PK) | numbered by created_at | Alert identifier |
| rule_id | TEXT | yes | `RULE-NNN` | – | no | FK → risk_rules; rule active and effective | Rule that fired |
| account_id | TEXT | yes | `ACC-CUS-NNNNNN-NN` | – | no | FK → accounts | Alerted (anchor) account |
| customer_id | TEXT | yes | `CUS-NNNNNN` | – | no | FK → customers; = owner of account_id | Alerted customer |
| created_at | TEXT | yes | `YYYY-MM-DDTHH:MM:SS` | timestamp | no | > every triggering transaction timestamp | Alert creation time |
| metric_value | REAL | yes | ≥ threshold_value | rule metric_unit | no | ≥ threshold_value | Measured metric |
| threshold_value | REAL | yes | > 0 | rule metric_unit | no | = risk_rules.threshold_value | Threshold in force |
| score | REAL | yes | 0–100 | points | no | = scoring formula(rule, metric_value) | Risk score |
| priority_rank | INTEGER | yes | 1..N | rank | yes | permutation of 1..N in ranking order | Review priority (1 = highest) |
| trigger_txn_count | INTEGER | yes | ≥ 1 | count | no | = number of alert_transactions rows | Number of triggering transactions |
| trigger_amount | REAL | yes | > 0 | EUR | no | = sum of triggering transaction amounts | Total triggering amount |
| reason | TEXT | yes | free text | – | no | non-empty; states facts, threshold and score arithmetic | Human-readable explanation |
| alert_status | TEXT | yes | OPEN, IN_REVIEW, CONFIRMED, FALSE_POSITIVE, ESCALATED | – | no | derived from analyst_actions | Derived review status |

## alert_transactions

Bridge table: N:M between alerts and their triggering transactions.

- **Primary key:** (`alert_id`, `transaction_id`). **Foreign keys:** `alert_id` → `alerts`, `transaction_id` → `transactions`.
- **Cardinality:** an alert has 1..n triggering transactions (exactly one PRIMARY); a transaction triggers 0..n alerts.
- **Effective dates:** none of its own (inherits the alert's `created_at`).
- **Lifecycle:** none; rows are written once when the alert is created.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| alert_id | TEXT | yes | `ALT-2026-NNNNNN` | – | with transaction_id (PK) | FK → alerts | Alert |
| transaction_id | TEXT | yes | `TXN-2026-NNNNNNNN` | – | with alert_id (PK) | FK → transactions; timestamp < alert created_at | Triggering transaction |
| trigger_role | TEXT | yes | PRIMARY, SUPPORTING | – | no | exactly one PRIMARY per alert (the transaction that completed the condition) | Role of the transaction in the alert |

## cases

Investigation unit grouping related alerts of one customer.

- **Primary key:** `case_id`. **Foreign keys:** `customer_id` → `customers`, `primary_account_id` → `accounts`.
- **Cardinality:** 1 case → 1..n alerts (via `case_alerts`), 1 customer, 1..n analyst actions.
- **Grouping rule:** alerts of the same customer whose `created_at` is within 14 days of the case's first alert share a case.
- **Effective dates:** `opened_at` (= created_at of the first alert); later lifecycle timestamps live in `analyst_actions`.
- **Lifecycle:** `case_status` derived: OPEN while any alert has no disposition; ESCALATED if all are dispositioned and any is ESCALATED; else CLOSED.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| case_id | TEXT | yes | `CASE-2026-NNNNNN` | – | yes (PK) | numbered by opened_at | Case identifier |
| customer_id | TEXT | yes | `CUS-NNNNNN` | – | no | FK → customers; owner of every alert in the case | Investigated customer |
| primary_account_id | TEXT | yes | `ACC-CUS-NNNNNN-NN` | – | no | FK → accounts; account of the best-ranked alert | Main account under review |
| opened_at | TEXT | yes | `YYYY-MM-DDTHH:MM:SS` | timestamp | no | = min created_at of its alerts | Case opening time |
| alert_count | INTEGER | yes | ≥ 1 | count | no | = number of case_alerts rows | Number of grouped alerts |
| max_score | REAL | yes | 0–100 | points | no | = max score of its alerts | Highest alert score |
| case_status | TEXT | yes | OPEN, CLOSED, ESCALATED | – | no | derived (see lifecycle) | Derived case state |

## case_alerts

Bridge table: N:M between cases and alerts.

- **Primary key:** (`case_id`, `alert_id`). **Foreign keys:** `case_id` → `cases`, `alert_id` → `alerts`.
- **Cardinality:** a case has 1..n alerts; the generator places each alert in exactly one case.
- **Effective dates:** `added_at` (= alert created_at). **Lifecycle:** none.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| case_id | TEXT | yes | `CASE-2026-NNNNNN` | – | with alert_id (PK) | FK → cases | Case |
| alert_id | TEXT | yes | `ALT-2026-NNNNNN` | – | with case_id (PK) | FK → alerts | Grouped alert |
| added_at | TEXT | yes | `YYYY-MM-DDTHH:MM:SS` | timestamp | no | = alert created_at; ≥ case opened_at | When the alert joined the case |

## analyst_actions

Append-only event table of case work: assignment, review and disposition.

- **Primary key:** `action_id`. **Alternate key:** (`case_id`, `action_seq`). **Foreign keys:** `case_id` → `cases`; `alert_id` → `alerts` (optional); (`case_id`, `alert_id`) → `case_alerts`.
- **Cardinality:** 1 case → 1 ASSIGN, then per alert 0..1 REVIEW followed by 0..1 DISPOSITION.
- **Optional relationships:** `alert_id` is null only for ASSIGN; `assigned_to` only for ASSIGN; `disposition` / `decision_basis` only for DISPOSITION.
- **Effective dates:** `action_timestamp`; non-decreasing in `action_seq` order; REVIEW/DISPOSITION ≥ the alert's created_at; nothing after the snapshot (2026-10-22T00:00:00).
- **Lifecycle:** the rows *are* the case/alert lifecycle: ASSIGN → REVIEW → DISPOSITION per alert; actions not yet due by the snapshot are absent, which leaves the alert IN_REVIEW / OPEN.
- **Disposition decision rule** (applied to the alert, documented in [`risk_rules.md`](risk_rules.md)): DR-1 FALSE_POSITIVE when the rule's mitigating condition holds and KYC risk is not HIGH; DR-2 CONFIRMED when the rule's aggravating condition holds and score ≥ 80; DR-3 ESCALATED otherwise. CONFIRMED is recorded by a RISK_REVIEWER, other dispositions by the assigned OPERATIONS_ANALYST.

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| action_id | TEXT | yes | `ACT-2026-NNNNNN` | – | yes (PK) | GLOB format | Action identifier |
| case_id | TEXT | yes | `CASE-2026-NNNNNN` | – | with action_seq | FK → cases | Case worked |
| alert_id | TEXT | no | `ALT-2026-NNNNNN` | – | no | FK → alerts and (case_id, alert_id) → case_alerts; null iff ASSIGN | Alert reviewed / dispositioned |
| action_seq | INTEGER | yes | 1..n | sequence | with case_id | consecutive from 1 per case | Order of the action within the case |
| action_type | TEXT | yes | ASSIGN, REVIEW, DISPOSITION | – | no | ASSIGN first; DISPOSITION after the alert's REVIEW | Kind of action |
| actor_role | TEXT | yes | SYSTEM, OPERATIONS_ANALYST, RISK_REVIEWER | – | no | ASSIGN: SYSTEM; REVIEW: OPERATIONS_ANALYST; DISPOSITION: RISK_REVIEWER iff CONFIRMED | Role performing the action |
| actor_id | TEXT | yes | SYSTEM, OPS-01..OPS-08, RISK-01..RISK-03 | – | no | consistent with actor_role | Synthetic user code |
| assigned_to | TEXT | no | OPS-01..OPS-08 | – | no | set iff ASSIGN | Analyst who owns the case |
| action_timestamp | TEXT | yes | `YYYY-MM-DDTHH:MM:SS` | timestamp | no | see effective dates | When the action happened |
| disposition | TEXT | no | CONFIRMED, FALSE_POSITIVE, ESCALATED | – | no | set iff DISPOSITION | Outcome of the review |
| decision_basis | TEXT | no | `DR-n: <facts>` | – | no | set iff DISPOSITION | Decision rule applied and the facts it used |

---

## ground_truth (evaluation file — not part of the schema)

`ground_truth.csv` / `ground_truth.json` label what the generator planted and injected. It is
written next to the tables but is **not** a table in `schema.sql`, none of its fields appear in
any input table, and no rule, feature or disposition code reads it (enforced by tests).

| Field | Type | Required | Allowed values / format | Units | Unique | Validation rule | Description |
|---|---|---|---|---|---|---|---|
| ground_truth_id | TEXT | yes | `GT-NNNNNN` | – | yes | sequential | Label identifier |
| category | TEXT | yes | PLANTED_PATTERN, FALSE_POSITIVE_PLANT, EDGE_CASE | – | no | enum | What kind of label this is |
| entity_type | TEXT | yes | ACCOUNT, TRANSACTION, TRANSFER_LINK, ALERT, ANALYST_ACTION | – | no | enum | Table of the labelled entity |
| entity_id | TEXT | yes | an existing identifier | – | with issue type | exists in the entity's table | Labelled entity |
| issue_or_pattern_type | TEXT | yes | VELOCITY_SPIKE, NEW_DEVICE_ACTIVITY, CIRCULAR_TRANSFER, DORMANT_REACTIVATION, HIGH_AMOUNT_VS_PROFILE (patterns); TXN_ON_CLOSED_ACCOUNT, NEGATIVE_BALANCE_NO_OVERDRAFT, TRANSFER_LEG_MISMATCH, ALERT_WITHOUT_TRIGGER, DISPOSITION_BEFORE_ALERT (edge cases) | – | no | enum | Pattern or issue type |
| expected_label | TEXT | yes | SUSPICIOUS, BENIGN, INVALID | – | no | SUSPICIOUS/BENIGN for patterns, INVALID for edge cases | Expected truth |
| expected_rule_id | TEXT | no | `RULE-NNN` | – | no | set for patterns | Rule expected to detect the pattern |
| notes | TEXT | yes | free text | – | no | non-empty | Key transactions / members and what was planted or injected |
