# Validation summary

Produced by the independent validator (`python -m generator.validator`), which reads only the exported CSV files and `schema.sql`.

**Total findings:** 20

## Reconciliation with ground truth

**PASS - findings are exactly the labelled edge cases**

| expected edge cases | findings | matched | missing | unexpected |
|---|---|---|---|---|
| 20 | 20 | 20 | 0 | 0 |

## Findings by check

| check | findings |
|---|---|
| ALERT_WITHOUT_TRIGGER | 4 |
| DISPOSITION_BEFORE_ALERT | 4 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | 4 |
| TRANSFER_LEG_MISMATCH | 4 |
| TXN_ON_CLOSED_ACCOUNT | 4 |

## Metrics

### row counts

| key | value |
|---|---|
| customers | 200 |
| kyc_profiles | 200 |
| accounts | 280 |
| merchants | 63 |
| devices | 276 |
| account_devices | 394 |
| account_status_history | 321 |
| risk_rules | 6 |
| transactions | 4000 |
| transfer_links | 142 |
| alerts | 40 |
| alert_transactions | 100 |
| cases | 39 |
| case_alerts | 40 |
| analyst_actions | 117 |

### alerts by rule

| key | value |
|---|---|
| RULE-001 | 3 |
| RULE-002 | 3 |
| RULE-003 | 3 |
| RULE-004 | 3 |
| RULE-005 | 28 |

### alert score

| key | value |
|---|---|
| min | 77.45 |
| median | 85.62 |
| max | 95.43 |

### alert status

| key | value |
|---|---|
| CONFIRMED | 10 |
| ESCALATED | 6 |
| FALSE_POSITIVE | 23 |
| OPEN | 1 |

### case status

| key | value |
|---|---|
| CLOSED | 32 |
| ESCALATED | 6 |
| OPEN | 1 |

### disposition false positive rate by rule

| key | value |
|---|---|
| RULE-001 | 0.3333 |
| RULE-002 | 0.3333 |
| RULE-003 | 0.3333 |
| RULE-004 | 0.3333 |
| RULE-005 | 0.7037 |

### planted pattern detection

| key | value |
|---|---|
| CIRCULAR_TRANSFER/BENIGN | {"planted": 1, "detected": 1} |
| CIRCULAR_TRANSFER/SUSPICIOUS | {"planted": 2, "detected": 2} |
| DORMANT_REACTIVATION/BENIGN | {"planted": 1, "detected": 1} |
| DORMANT_REACTIVATION/SUSPICIOUS | {"planted": 2, "detected": 2} |
| HIGH_AMOUNT_VS_PROFILE/BENIGN | {"planted": 34, "detected": 25} |
| HIGH_AMOUNT_VS_PROFILE/SUSPICIOUS | {"planted": 2, "detected": 2} |
| NEW_DEVICE_ACTIVITY/BENIGN | {"planted": 1, "detected": 1} |
| NEW_DEVICE_ACTIVITY/SUSPICIOUS | {"planted": 2, "detected": 2} |
| VELOCITY_SPIKE/BENIGN | {"planted": 1, "detected": 1} |
| VELOCITY_SPIKE/SUSPICIOUS | {"planted": 2, "detected": 2} |

### ground truth false positive rate by rule

| key | value |
|---|---|
| RULE-001 | 0.3333 |
| RULE-002 | 0.3333 |
| RULE-003 | 0.3333 |
| RULE-004 | 0.3333 |
| RULE-005 | 0.9286 |

### disposition vs ground truth

| key | value |
|---|---|
| BENIGN->ESCALATED | 6 |
| BENIGN->FALSE_POSITIVE | 23 |
| BENIGN->NOT_YET_DISPOSITIONED | 1 |
| SUSPICIOUS->CONFIRMED | 10 |

## Findings

| check | table | record | detail |
|---|---|---|---|
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000002 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000010 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000023 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000030 | no alert_transactions rows |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-000007 | CASE-2026-000003 #3 DISPOSITION at 2026-02-02T13:45:35 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-000072 | CASE-2026-000024 #3 DISPOSITION at 2026-07-10T15:59:47 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-000112 | CASE-2026-000038 #3 DISPOSITION at 2026-10-04T00:30:00 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-000117 | CASE-2026-000039 #3 DISPOSITION at 2026-10-09T15:47:09 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00003989 | ACC-CUS-000067-01: before 1637.33 DEBIT 1896.79 -> after -259.46 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00003990 | ACC-CUS-000126-02: before 43537.96 DEBIT 43847.21 -> after -309.25 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00003991 | ACC-CUS-000170-01: before 3263.74 DEBIT 3436.45 -> after -172.71 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00003992 | ACC-CUS-000173-02: before 17836.60 DEBIT 18106.71 -> after -270.11 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-000139 | link 1668.36, debit leg 1668.36, credit leg 1641.37 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-000140 | link 2962.29, debit leg 2962.29, credit leg 2939.32 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-000141 | link 953.56, debit leg 953.56, credit leg 939.57 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-000142 | link 1637.08, debit leg 1637.08, credit leg 1587.17 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00003985 | ACC-CUS-000009-01 at 2026-06-13T06:26:55 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00003986 | ACC-CUS-000054-02 at 2026-05-18T22:09:43 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00003987 | ACC-CUS-000122-01 at 2026-07-15T08:20:10 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00003988 | ACC-CUS-000140-01 at 2026-02-26T19:00:22 |
