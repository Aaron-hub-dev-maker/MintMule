-- Synthetic fraud / AML analyst-review dataset: SQLite schema (authoritative contract).
-- Every row in this database is synthetic. No real personal data, no external data.
-- Conventions: timestamps are UTC 'YYYY-MM-DDTHH:MM:SS', dates 'YYYY-MM-DD',
-- money is EUR with 2 decimals, identifiers are zero-padded and immutable.
-- Tables are created in foreign-key dependency order (parents first).

PRAGMA foreign_keys = ON;

CREATE TABLE customers (
    customer_id      TEXT    NOT NULL PRIMARY KEY CHECK (customer_id GLOB 'CUS-[0-9][0-9][0-9][0-9][0-9][0-9]'),
    customer_type    TEXT    NOT NULL CHECK (customer_type IN ('INDIVIDUAL','BUSINESS')),
    display_name     TEXT    NOT NULL,
    birth_year       INTEGER CHECK (birth_year BETWEEN 1900 AND 2010),
    country_code     TEXT    NOT NULL CHECK (country_code IN ('AT','BE','DE','ES','FR','IE','IT','LU','NL','PT')),
    segment          TEXT    NOT NULL CHECK (segment IN ('RETAIL','AFFLUENT','SMALL_BUSINESS','CORPORATE')),
    onboarding_date  TEXT    NOT NULL CHECK (onboarding_date LIKE '____-__-__'),
    customer_status  TEXT    NOT NULL CHECK (customer_status IN ('ACTIVE','DORMANT','CLOSED'))
);

CREATE TABLE kyc_profiles (
    kyc_id                   TEXT    NOT NULL PRIMARY KEY CHECK (kyc_id GLOB 'KYC-[0-9][0-9][0-9][0-9][0-9][0-9]'),
    customer_id              TEXT    NOT NULL UNIQUE,
    risk_rating              TEXT    NOT NULL CHECK (risk_rating IN ('LOW','MEDIUM','HIGH')),
    pep_flag                 INTEGER NOT NULL CHECK (pep_flag IN (0,1)),
    occupation_or_industry   TEXT    NOT NULL CHECK (occupation_or_industry IN ('EMPLOYED','SELF_EMPLOYED','RETIRED','STUDENT','UNEMPLOYED','RETAIL_TRADE','PROFESSIONAL_SERVICES','HOSPITALITY','CONSTRUCTION','IT_SERVICES','WHOLESALE')),
    expected_monthly_volume  REAL    NOT NULL CHECK (expected_monthly_volume > 0),
    expected_max_txn_amount  REAL    NOT NULL CHECK (expected_max_txn_amount > 0),
    verified_date            TEXT    NOT NULL CHECK (verified_date LIKE '____-__-__'),
    next_review_date         TEXT    NOT NULL CHECK (next_review_date LIKE '____-__-__'),
    kyc_status               TEXT    NOT NULL CHECK (kyc_status IN ('CURRENT','REVIEW_OVERDUE')),
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CHECK (next_review_date > verified_date)
);

CREATE TABLE accounts (
    account_id       TEXT    NOT NULL PRIMARY KEY CHECK (account_id GLOB 'ACC-CUS-[0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    customer_id      TEXT    NOT NULL,
    account_seq      INTEGER NOT NULL CHECK (account_seq BETWEEN 1 AND 99),
    account_type     TEXT    NOT NULL CHECK (account_type IN ('CURRENT','SAVINGS','BUSINESS_CURRENT')),
    currency         TEXT    NOT NULL CHECK (currency IN ('EUR')),
    open_date        TEXT    NOT NULL CHECK (open_date LIKE '____-__-__'),
    close_date       TEXT    CHECK (close_date LIKE '____-__-__'),
    overdraft_limit  REAL    CHECK (overdraft_limit > 0),
    opening_balance  REAL    NOT NULL,
    account_status   TEXT    NOT NULL CHECK (account_status IN ('ACTIVE','DORMANT','CLOSED')),
    UNIQUE (customer_id, account_seq),
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CHECK (substr(account_id, 5, 10) = customer_id),
    CHECK (CAST(substr(account_id, 16, 2) AS INTEGER) = account_seq),
    CHECK (close_date IS NULL OR close_date >= open_date)
);

CREATE TABLE merchants (
    merchant_id     TEXT NOT NULL PRIMARY KEY CHECK (merchant_id GLOB 'MER-[0-9][0-9][0-9][0-9][0-9][0-9]'),
    merchant_name   TEXT NOT NULL,
    category        TEXT NOT NULL CHECK (category IN ('GROCERY','RESTAURANT','TRANSPORT','FUEL','UTILITIES','TELECOM','ONLINE_RETAIL','GENERAL_RETAIL','ELECTRONICS','TRAVEL','FURNITURE','HOME_IMPROVEMENT','AUTOMOTIVE','INSURANCE','HEALTH','ENTERTAINMENT','JEWELRY','GIFT_CARDS','CRYPTO_EXCHANGE','MONEY_SERVICES','GAMBLING')),
    risk_tier       TEXT NOT NULL CHECK (risk_tier IN ('STANDARD','ELEVATED')),
    country_code    TEXT NOT NULL CHECK (country_code IN ('AT','BE','DE','ES','FR','IE','IT','LU','NL','PT')),
    onboarded_date  TEXT NOT NULL CHECK (onboarded_date LIKE '____-__-__')
);

CREATE TABLE devices (
    device_id            TEXT NOT NULL PRIMARY KEY CHECK (device_id GLOB 'DEV-[0-9][0-9][0-9][0-9][0-9][0-9]'),
    device_type          TEXT NOT NULL CHECK (device_type IN ('MOBILE_IOS','MOBILE_ANDROID','TABLET','DESKTOP_WEB')),
    os_family            TEXT NOT NULL CHECK (os_family IN ('IOS','ANDROID','WINDOWS','MACOS','LINUX')),
    first_registered_at  TEXT NOT NULL CHECK (first_registered_at LIKE '____-__-__T__:__:__')
);

CREATE TABLE account_devices (
    account_id  TEXT NOT NULL,
    device_id   TEXT NOT NULL,
    first_seen  TEXT NOT NULL CHECK (first_seen LIKE '____-__-__T__:__:__'),
    last_seen   TEXT NOT NULL CHECK (last_seen LIKE '____-__-__T__:__:__'),
    PRIMARY KEY (account_id, device_id),
    FOREIGN KEY (account_id) REFERENCES accounts (account_id),
    FOREIGN KEY (device_id) REFERENCES devices (device_id),
    CHECK (last_seen >= first_seen)
);

CREATE TABLE account_status_history (
    status_event_id  TEXT NOT NULL PRIMARY KEY CHECK (status_event_id GLOB 'ASH-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    account_id       TEXT NOT NULL,
    event_timestamp  TEXT NOT NULL CHECK (event_timestamp LIKE '____-__-__T__:__:__'),
    from_status      TEXT CHECK (from_status IN ('ACTIVE','DORMANT','CLOSED')),
    to_status        TEXT NOT NULL CHECK (to_status IN ('ACTIVE','DORMANT','CLOSED')),
    reason_code      TEXT NOT NULL CHECK (reason_code IN ('ACCOUNT_OPENED','INACTIVITY_180_DAYS','REACTIVATED_ONLINE','REACTIVATED_IN_BRANCH','CUSTOMER_REQUEST')),
    changed_by_role  TEXT NOT NULL CHECK (changed_by_role IN ('SYSTEM','CUSTOMER','OPERATIONS_ANALYST')),
    FOREIGN KEY (account_id) REFERENCES accounts (account_id),
    CHECK (from_status IS NULL OR from_status <> to_status)
);

CREATE TABLE risk_rules (
    rule_id           TEXT    NOT NULL PRIMARY KEY CHECK (rule_id GLOB 'RULE-[0-9][0-9][0-9]'),
    rule_code         TEXT    NOT NULL UNIQUE,
    rule_name         TEXT    NOT NULL,
    description       TEXT    NOT NULL,
    applies_to        TEXT    NOT NULL,
    metric_name       TEXT    NOT NULL,
    metric_unit       TEXT    NOT NULL CHECK (metric_unit IN ('COUNT','EUR','RATIO')),
    lookback_hours    INTEGER NOT NULL CHECK (lookback_hours >= 0),
    threshold_value   REAL    NOT NULL CHECK (threshold_value > 0),
    midpoint_value    REAL    NOT NULL,
    weight            REAL    NOT NULL CHECK (weight BETWEEN 0 AND 100),
    score_ceiling     REAL    NOT NULL CHECK (score_ceiling <= 100),
    rule_version      INTEGER NOT NULL CHECK (rule_version >= 1),
    effective_from    TEXT    NOT NULL CHECK (effective_from LIKE '____-__-__'),
    effective_to      TEXT    CHECK (effective_to LIKE '____-__-__'),
    is_active         INTEGER NOT NULL CHECK (is_active IN (0,1)),
    owner_role        TEXT    NOT NULL CHECK (owner_role IN ('SYSTEM_ADMINISTRATOR')),
    approved_by_role  TEXT    NOT NULL CHECK (approved_by_role IN ('RISK_REVIEWER')),
    CHECK (midpoint_value > threshold_value),
    CHECK (score_ceiling > weight),
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE TABLE transactions (
    transaction_id  TEXT NOT NULL PRIMARY KEY CHECK (transaction_id GLOB 'TXN-2026-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    account_id      TEXT NOT NULL,
    txn_timestamp   TEXT NOT NULL CHECK (txn_timestamp LIKE '____-__-__T__:__:__'),
    direction       TEXT NOT NULL CHECK (direction IN ('DEBIT','CREDIT')),
    txn_type        TEXT NOT NULL CHECK (txn_type IN ('PURCHASE','ATM_WITHDRAWAL','BILL_PAYMENT','FEE','TRANSFER_OUT','ACCOUNT_CLOSURE','SALARY','DEPOSIT','REFUND','INTEREST','TRANSFER_IN')),
    channel         TEXT NOT NULL CHECK (channel IN ('CARD_POS','ECOMMERCE','ATM','MOBILE_APP','ONLINE_BANKING','BRANCH','SYSTEM')),
    amount          REAL NOT NULL CHECK (amount > 0),
    currency        TEXT NOT NULL CHECK (currency IN ('EUR')),
    balance_before  REAL NOT NULL,
    balance_after   REAL NOT NULL,
    merchant_id     TEXT,
    device_id       TEXT,
    FOREIGN KEY (account_id) REFERENCES accounts (account_id),
    FOREIGN KEY (merchant_id) REFERENCES merchants (merchant_id),
    FOREIGN KEY (device_id) REFERENCES devices (device_id),
    FOREIGN KEY (account_id, device_id) REFERENCES account_devices (account_id, device_id),
    CHECK ((direction = 'DEBIT' AND txn_type IN ('PURCHASE','ATM_WITHDRAWAL','BILL_PAYMENT','FEE','TRANSFER_OUT','ACCOUNT_CLOSURE'))
        OR (direction = 'CREDIT' AND txn_type IN ('SALARY','DEPOSIT','REFUND','INTEREST','TRANSFER_IN'))),
    CHECK (abs(balance_after - balance_before - (CASE direction WHEN 'CREDIT' THEN amount ELSE -amount END)) < 0.005)
);

CREATE TABLE transfer_links (
    transfer_id        TEXT NOT NULL PRIMARY KEY CHECK (transfer_id GLOB 'TRF-2026-[0-9][0-9][0-9][0-9][0-9][0-9]'),
    source_txn_id      TEXT NOT NULL UNIQUE,
    dest_txn_id        TEXT NOT NULL UNIQUE,
    source_account_id  TEXT NOT NULL,
    dest_account_id    TEXT NOT NULL,
    amount             REAL NOT NULL CHECK (amount > 0),
    initiated_at       TEXT NOT NULL CHECK (initiated_at LIKE '____-__-__T__:__:__'),
    transfer_scope     TEXT NOT NULL CHECK (transfer_scope IN ('OWN_ACCOUNTS','THIRD_PARTY')),
    FOREIGN KEY (source_txn_id) REFERENCES transactions (transaction_id),
    FOREIGN KEY (dest_txn_id) REFERENCES transactions (transaction_id),
    FOREIGN KEY (source_account_id) REFERENCES accounts (account_id),
    FOREIGN KEY (dest_account_id) REFERENCES accounts (account_id),
    CHECK (source_account_id <> dest_account_id)
);

CREATE TABLE alerts (
    alert_id           TEXT    NOT NULL PRIMARY KEY CHECK (alert_id GLOB 'ALT-2026-[0-9][0-9][0-9][0-9][0-9][0-9]'),
    rule_id            TEXT    NOT NULL,
    account_id         TEXT    NOT NULL,
    customer_id        TEXT    NOT NULL,
    created_at         TEXT    NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__'),
    metric_value       REAL    NOT NULL,
    threshold_value    REAL    NOT NULL,
    score              REAL    NOT NULL CHECK (score BETWEEN 0 AND 100),
    priority_rank      INTEGER NOT NULL UNIQUE CHECK (priority_rank >= 1),
    trigger_txn_count  INTEGER NOT NULL CHECK (trigger_txn_count >= 1),
    trigger_amount     REAL    NOT NULL CHECK (trigger_amount > 0),
    reason             TEXT    NOT NULL,
    alert_status       TEXT    NOT NULL CHECK (alert_status IN ('OPEN','IN_REVIEW','CONFIRMED','FALSE_POSITIVE','ESCALATED')),
    FOREIGN KEY (rule_id) REFERENCES risk_rules (rule_id),
    FOREIGN KEY (account_id) REFERENCES accounts (account_id),
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CHECK (substr(account_id, 5, 10) = customer_id),
    CHECK (metric_value >= threshold_value)
);

CREATE TABLE alert_transactions (
    alert_id        TEXT NOT NULL,
    transaction_id  TEXT NOT NULL,
    trigger_role    TEXT NOT NULL CHECK (trigger_role IN ('PRIMARY','SUPPORTING')),
    PRIMARY KEY (alert_id, transaction_id),
    FOREIGN KEY (alert_id) REFERENCES alerts (alert_id),
    FOREIGN KEY (transaction_id) REFERENCES transactions (transaction_id)
);

CREATE TABLE cases (
    case_id             TEXT    NOT NULL PRIMARY KEY CHECK (case_id GLOB 'CASE-2026-[0-9][0-9][0-9][0-9][0-9][0-9]'),
    customer_id         TEXT    NOT NULL,
    primary_account_id  TEXT    NOT NULL,
    opened_at           TEXT    NOT NULL CHECK (opened_at LIKE '____-__-__T__:__:__'),
    alert_count         INTEGER NOT NULL CHECK (alert_count >= 1),
    max_score           REAL    NOT NULL CHECK (max_score BETWEEN 0 AND 100),
    case_status         TEXT    NOT NULL CHECK (case_status IN ('OPEN','CLOSED','ESCALATED')),
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    FOREIGN KEY (primary_account_id) REFERENCES accounts (account_id),
    CHECK (substr(primary_account_id, 5, 10) = customer_id)
);

CREATE TABLE case_alerts (
    case_id   TEXT NOT NULL,
    alert_id  TEXT NOT NULL,
    added_at  TEXT NOT NULL CHECK (added_at LIKE '____-__-__T__:__:__'),
    PRIMARY KEY (case_id, alert_id),
    FOREIGN KEY (case_id) REFERENCES cases (case_id),
    FOREIGN KEY (alert_id) REFERENCES alerts (alert_id)
);

CREATE TABLE analyst_actions (
    action_id         TEXT    NOT NULL PRIMARY KEY CHECK (action_id GLOB 'ACT-2026-[0-9][0-9][0-9][0-9][0-9][0-9]'),
    case_id           TEXT    NOT NULL,
    alert_id          TEXT,
    action_seq        INTEGER NOT NULL CHECK (action_seq >= 1),
    action_type       TEXT    NOT NULL CHECK (action_type IN ('ASSIGN','REVIEW','DISPOSITION')),
    actor_role        TEXT    NOT NULL CHECK (actor_role IN ('SYSTEM','OPERATIONS_ANALYST','RISK_REVIEWER')),
    actor_id          TEXT    NOT NULL,
    assigned_to       TEXT,
    action_timestamp  TEXT    NOT NULL CHECK (action_timestamp LIKE '____-__-__T__:__:__'),
    disposition       TEXT    CHECK (disposition IN ('CONFIRMED','FALSE_POSITIVE','ESCALATED')),
    decision_basis    TEXT,
    UNIQUE (case_id, action_seq),
    FOREIGN KEY (case_id) REFERENCES cases (case_id),
    FOREIGN KEY (alert_id) REFERENCES alerts (alert_id),
    FOREIGN KEY (case_id, alert_id) REFERENCES case_alerts (case_id, alert_id),
    CHECK ((action_type = 'ASSIGN') = (alert_id IS NULL)),
    CHECK ((action_type = 'ASSIGN') = (assigned_to IS NOT NULL)),
    CHECK ((action_type = 'DISPOSITION') = (disposition IS NOT NULL)),
    CHECK ((action_type = 'DISPOSITION') = (decision_basis IS NOT NULL))
);

-- Event tables are append-only: history is never overwritten or deleted.
CREATE TRIGGER account_status_history_no_update BEFORE UPDATE ON account_status_history
BEGIN SELECT RAISE(ABORT, 'account_status_history is append-only'); END;
CREATE TRIGGER account_status_history_no_delete BEFORE DELETE ON account_status_history
BEGIN SELECT RAISE(ABORT, 'account_status_history is append-only'); END;
CREATE TRIGGER analyst_actions_no_update BEFORE UPDATE ON analyst_actions
BEGIN SELECT RAISE(ABORT, 'analyst_actions is append-only'); END;
CREATE TRIGGER analyst_actions_no_delete BEFORE DELETE ON analyst_actions
BEGIN SELECT RAISE(ABORT, 'analyst_actions is append-only'); END;
