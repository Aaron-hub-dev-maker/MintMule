# Entity-relationship diagram

Generated from `generator/schema.sql` by `python -m generator.schema`; do not edit by hand.
Crow's-foot notation: `||` exactly one, `|o` / `o|` zero or one, `o{` zero or many.
A `|o` on the parent side marks an optional (nullable) foreign key.

```mermaid
erDiagram
    customers ||--o| kyc_profiles : "customer_id"
    customers ||--o{ accounts : "customer_id"
    accounts ||--o{ account_devices : "account_id"
    devices ||--o{ account_devices : "device_id"
    accounts ||--o{ account_status_history : "account_id"
    accounts ||--o{ transactions : "account_id"
    merchants |o--o{ transactions : "merchant_id"
    devices |o--o{ transactions : "device_id"
    account_devices |o--o{ transactions : "account_id, device_id"
    transactions ||--o| transfer_links : "source_txn_id"
    transactions ||--o| transfer_links : "dest_txn_id"
    accounts ||--o{ transfer_links : "source_account_id"
    accounts ||--o{ transfer_links : "dest_account_id"
    risk_rules ||--o{ alerts : "rule_id"
    accounts ||--o{ alerts : "account_id"
    customers ||--o{ alerts : "customer_id"
    alerts ||--o{ alert_transactions : "alert_id"
    transactions ||--o{ alert_transactions : "transaction_id"
    customers ||--o{ cases : "customer_id"
    accounts ||--o{ cases : "primary_account_id"
    cases ||--o{ case_alerts : "case_id"
    alerts ||--o{ case_alerts : "alert_id"
    cases ||--o{ analyst_actions : "case_id"
    alerts |o--o{ analyst_actions : "alert_id"
    case_alerts |o--o{ analyst_actions : "case_id, alert_id"
    customers {
        TEXT customer_id PK
        TEXT customer_type
        TEXT display_name
        INTEGER birth_year
        TEXT country_code
        TEXT segment
        TEXT onboarding_date
        TEXT customer_status
    }
    kyc_profiles {
        TEXT kyc_id PK
        TEXT customer_id FK
        TEXT risk_rating
        INTEGER pep_flag
        TEXT occupation_or_industry
        REAL expected_monthly_volume
        REAL expected_max_txn_amount
        TEXT verified_date
        TEXT next_review_date
        TEXT kyc_status
    }
    accounts {
        TEXT account_id PK
        TEXT customer_id FK
        INTEGER account_seq
        TEXT account_type
        TEXT currency
        TEXT open_date
        TEXT close_date
        REAL overdraft_limit
        REAL opening_balance
        TEXT account_status
    }
    merchants {
        TEXT merchant_id PK
        TEXT merchant_name
        TEXT category
        TEXT risk_tier
        TEXT country_code
        TEXT onboarded_date
    }
    devices {
        TEXT device_id PK
        TEXT device_type
        TEXT os_family
        TEXT first_registered_at
    }
    account_devices {
        TEXT account_id PK,FK
        TEXT device_id PK,FK
        TEXT first_seen
        TEXT last_seen
    }
    account_status_history {
        TEXT status_event_id PK
        TEXT account_id FK
        TEXT event_timestamp
        TEXT from_status
        TEXT to_status
        TEXT reason_code
        TEXT changed_by_role
    }
    risk_rules {
        TEXT rule_id PK
        TEXT rule_code
        TEXT rule_name
        TEXT description
        TEXT applies_to
        TEXT metric_name
        TEXT metric_unit
        INTEGER lookback_hours
        REAL threshold_value
        REAL midpoint_value
        REAL weight
        REAL score_ceiling
        INTEGER rule_version
        TEXT effective_from
        TEXT effective_to
        INTEGER is_active
        TEXT owner_role
        TEXT approved_by_role
    }
    transactions {
        TEXT transaction_id PK
        TEXT account_id FK
        TEXT txn_timestamp
        TEXT direction
        TEXT txn_type
        TEXT channel
        REAL amount
        TEXT currency
        REAL balance_before
        REAL balance_after
        TEXT merchant_id FK
        TEXT device_id FK
    }
    transfer_links {
        TEXT transfer_id PK
        TEXT source_txn_id FK
        TEXT dest_txn_id FK
        TEXT source_account_id FK
        TEXT dest_account_id FK
        REAL amount
        TEXT initiated_at
        TEXT transfer_scope
    }
    alerts {
        TEXT alert_id PK
        TEXT rule_id FK
        TEXT account_id FK
        TEXT customer_id FK
        TEXT created_at
        REAL metric_value
        REAL threshold_value
        REAL score
        INTEGER priority_rank
        INTEGER trigger_txn_count
        REAL trigger_amount
        TEXT reason
        TEXT alert_status
    }
    alert_transactions {
        TEXT alert_id PK,FK
        TEXT transaction_id PK,FK
        TEXT trigger_role
    }
    cases {
        TEXT case_id PK
        TEXT customer_id FK
        TEXT primary_account_id FK
        TEXT opened_at
        INTEGER alert_count
        REAL max_score
        TEXT case_status
    }
    case_alerts {
        TEXT case_id PK,FK
        TEXT alert_id PK,FK
        TEXT added_at
    }
    analyst_actions {
        TEXT action_id PK
        TEXT case_id FK
        TEXT alert_id FK
        INTEGER action_seq
        TEXT action_type
        TEXT actor_role
        TEXT actor_id
        TEXT assigned_to
        TEXT action_timestamp
        TEXT disposition
        TEXT decision_basis
    }
```
