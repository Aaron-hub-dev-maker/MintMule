# Validation summary

Produced by the independent validator (`python -m generator.validator`), which reads only the exported CSV files and `schema.sql`.

**Total findings:** 50

## Reconciliation with ground truth

**PASS - findings are exactly the labelled edge cases**

| expected edge cases | findings | matched | missing | unexpected |
|---|---|---|---|---|
| 50 | 50 | 50 | 0 | 0 |

## Findings by check

| check | findings |
|---|---|
| ALERT_WITHOUT_TRIGGER | 10 |
| DISPOSITION_BEFORE_ALERT | 10 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | 10 |
| TRANSFER_LEG_MISMATCH | 10 |
| TXN_ON_CLOSED_ACCOUNT | 10 |

## Metrics

### row counts

| key | value |
|---|---|
| customers | 5000 |
| kyc_profiles | 5000 |
| accounts | 7000 |
| merchants | 500 |
| devices | 6810 |
| account_devices | 9514 |
| account_status_history | 8010 |
| risk_rules | 6 |
| transactions | 100000 |
| transfer_links | 3299 |
| alerts | 1000 |
| alert_transactions | 2241 |
| cases | 959 |
| case_alerts | 1000 |
| analyst_actions | 2930 |

### alerts by rule

| key | value |
|---|---|
| RULE-001 | 65 |
| RULE-002 | 66 |
| RULE-003 | 65 |
| RULE-004 | 65 |
| RULE-005 | 739 |

### alert score

| key | value |
|---|---|
| min | 69.95 |
| median | 84.55 |
| max | 95.49 |

### alert status

| key | value |
|---|---|
| CONFIRMED | 197 |
| ESCALATED | 172 |
| FALSE_POSITIVE | 616 |
| IN_REVIEW | 1 |
| OPEN | 14 |

### case status

| key | value |
|---|---|
| CLOSED | 779 |
| ESCALATED | 165 |
| OPEN | 15 |

### disposition false positive rate by rule

| key | value |
|---|---|
| RULE-001 | 0.3281 |
| RULE-002 | 0.3385 |
| RULE-003 | 0.2812 |
| RULE-004 | 0.2969 |
| RULE-005 | 0.7363 |

### planted pattern detection

| key | value |
|---|---|
| CIRCULAR_TRANSFER/BENIGN | {"planted": 25, "detected": 25} |
| CIRCULAR_TRANSFER/SUSPICIOUS | {"planted": 40, "detected": 40} |
| DORMANT_REACTIVATION/BENIGN | {"planted": 25, "detected": 25} |
| DORMANT_REACTIVATION/SUSPICIOUS | {"planted": 40, "detected": 40} |
| HIGH_AMOUNT_VS_PROFILE/BENIGN | {"planted": 850, "detected": 659} |
| HIGH_AMOUNT_VS_PROFILE/SUSPICIOUS | {"planted": 40, "detected": 40} |
| NEW_DEVICE_ACTIVITY/BENIGN | {"planted": 25, "detected": 25} |
| NEW_DEVICE_ACTIVITY/SUSPICIOUS | {"planted": 40, "detected": 40} |
| VELOCITY_SPIKE/BENIGN | {"planted": 25, "detected": 25} |
| VELOCITY_SPIKE/SUSPICIOUS | {"planted": 40, "detected": 40} |

### ground truth false positive rate by rule

| key | value |
|---|---|
| RULE-001 | 0.3846 |
| RULE-002 | 0.3939 |
| RULE-003 | 0.3846 |
| RULE-004 | 0.3846 |
| RULE-005 | 0.9459 |

### disposition vs ground truth

| key | value |
|---|---|
| BENIGN->CONFIRMED | 1 |
| BENIGN->ESCALATED | 172 |
| BENIGN->FALSE_POSITIVE | 616 |
| BENIGN->NOT_YET_DISPOSITIONED | 11 |
| SUSPICIOUS->CONFIRMED | 196 |
| SUSPICIOUS->NOT_YET_DISPOSITIONED | 4 |

## Findings

| check | table | record | detail |
|---|---|---|---|
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000045 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000210 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000264 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000349 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000442 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000651 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000659 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000725 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000926 | no alert_transactions rows |
| ALERT_WITHOUT_TRIGGER | alerts | ALT-2026-000987 | no alert_transactions rows |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-000245 | CASE-2026-000089 #3 DISPOSITION at 2026-02-11T23:29:20 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-000632 | CASE-2026-000220 #3 DISPOSITION at 2026-03-16T13:05:40 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-000674 | CASE-2026-000231 #3 DISPOSITION at 2026-03-18T10:31:09 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-001175 | CASE-2026-000390 #3 DISPOSITION at 2026-05-09T22:18:00 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-001275 | CASE-2026-000433 #3 DISPOSITION at 2026-05-17T13:55:31 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-001479 | CASE-2026-000409 #3 DISPOSITION at 2026-05-16T01:48:42 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-002006 | CASE-2026-000650 #3 DISPOSITION at 2026-07-22T19:42:53 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-002103 | CASE-2026-000688 #3 DISPOSITION at 2026-07-31T17:29:04 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-002225 | CASE-2026-000725 #3 DISPOSITION at 2026-08-12T18:24:54 |
| DISPOSITION_BEFORE_ALERT | analyst_actions | ACT-2026-002637 | CASE-2026-000862 #3 DISPOSITION at 2026-09-17T07:02:54 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00099971 | ACC-CUS-000231-02: before 13573.03 DEBIT 13706.03 -> after -133.00 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00099972 | ACC-CUS-001028-01: before 2856.72 DEBIT 2960.79 -> after -104.07 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00099973 | ACC-CUS-001140-01: before 2798.17 DEBIT 3084.12 -> after -285.95 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00099974 | ACC-CUS-001641-01: before 8663.62 DEBIT 8771.18 -> after -107.56 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00099975 | ACC-CUS-001738-02: before 8462.11 DEBIT 8652.19 -> after -190.08 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00099976 | ACC-CUS-003139-03: before 32387.17 DEBIT 32461.10 -> after -73.93 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00099977 | ACC-CUS-003897-01: before 5400.42 DEBIT 5451.39 -> after -50.97 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00099978 | ACC-CUS-003954-04: before 20879.98 DEBIT 21040.02 -> after -160.04 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00099979 | ACC-CUS-004853-02: before 32810.27 DEBIT 32887.51 -> after -77.24 |
| NEGATIVE_BALANCE_NO_OVERDRAFT | transactions | TXN-2026-00099980 | ACC-CUS-004911-01: before 3209.77 DEBIT 3430.48 -> after -220.71 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-003290 | link 6172.78, debit leg 6172.78, credit leg 6144.60 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-003291 | link 340138.07, debit leg 340138.07, credit leg 340117.84 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-003292 | link 406.54, debit leg 406.54, credit leg 396.80 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-003293 | link 1074.69, debit leg 1074.69, credit leg 1025.59 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-003294 | link 3071.71, debit leg 3071.71, credit leg 3040.68 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-003295 | link 4088.51, debit leg 4088.51, credit leg 4061.03 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-003296 | link 1907.05, debit leg 1907.05, credit leg 1871.27 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-003297 | link 1267.03, debit leg 1267.03, credit leg 1231.44 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-003298 | link 2510.90, debit leg 2510.90, credit leg 2490.70 |
| TRANSFER_LEG_MISMATCH | transfer_links | TRF-2026-003299 | link 3682.42, debit leg 3682.42, credit leg 3645.15 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00099961 | ACC-CUS-000164-01 at 2026-08-14T11:27:30 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00099962 | ACC-CUS-000475-01 at 2026-05-07T08:43:41 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00099963 | ACC-CUS-000922-04 at 2026-05-29T09:44:58 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00099964 | ACC-CUS-001821-01 at 2026-09-14T17:50:21 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00099965 | ACC-CUS-003920-01 at 2026-07-16T09:06:06 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00099966 | ACC-CUS-003923-01 at 2026-10-12T23:09:09 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00099967 | ACC-CUS-003954-01 at 2026-07-29T01:19:28 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00099968 | ACC-CUS-004213-01 at 2026-02-03T23:16:06 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00099969 | ACC-CUS-004485-01 at 2026-10-05T03:17:03 |
| TXN_ON_CLOSED_ACCOUNT | transactions | TXN-2026-00099970 | ACC-CUS-004878-01 at 2026-08-24T17:29:06 |
