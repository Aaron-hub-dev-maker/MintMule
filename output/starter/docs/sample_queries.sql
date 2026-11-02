-- Sample analyst queries for the synthetic AML review database (SQLite).
-- Run with: python -m generator.load_sqlite <dataset folder>

-- Q1: top 10 alerts by score (priority_rank 1 = review first, equal scores: older created_at first, then alert_id)
SELECT a.priority_rank, a.alert_id, r.rule_code, a.account_id, a.score, a.created_at, a.alert_status,
       substr(a.reason, 1, 80) AS reason
FROM alerts a JOIN risk_rules r ON r.rule_id = a.rule_id
ORDER BY a.priority_rank
LIMIT 10;

-- Q2: false-positive rate per rule (alerts with a disposition)
SELECT a.rule_id, r.rule_code,
       COUNT(*) AS dispositioned,
       SUM(d.disposition = 'FALSE_POSITIVE') AS false_positives,
       ROUND(1.0 * SUM(d.disposition = 'FALSE_POSITIVE') / COUNT(*), 3) AS false_positive_rate
FROM alerts a
JOIN risk_rules r ON r.rule_id = a.rule_id
JOIN analyst_actions d ON d.alert_id = a.alert_id AND d.action_type = 'DISPOSITION'
GROUP BY a.rule_id, r.rule_code
ORDER BY a.rule_id;

-- Q3: why is the top alert ranked first? its triggering transactions and decision
SELECT a.alert_id, a.score, at.trigger_role, t.transaction_id, t.txn_timestamp, t.txn_type, t.amount,
       t.merchant_id, t.device_id, d.disposition, d.decision_basis
FROM alerts a
JOIN alert_transactions at ON at.alert_id = a.alert_id
JOIN transactions t ON t.transaction_id = at.transaction_id
LEFT JOIN analyst_actions d ON d.alert_id = a.alert_id AND d.action_type = 'DISPOSITION'
WHERE a.priority_rank = 1
ORDER BY t.txn_timestamp;

-- Q4: data-quality exceptions visible in SQL (negative balance without overdraft, postings after closure)
SELECT 'NEGATIVE_BALANCE_NO_OVERDRAFT' AS issue, t.transaction_id, t.account_id, t.txn_timestamp, t.balance_after
FROM transactions t JOIN accounts ac ON ac.account_id = t.account_id
WHERE t.balance_after < 0 AND ac.overdraft_limit IS NULL
UNION ALL
SELECT 'TXN_ON_CLOSED_ACCOUNT', t.transaction_id, t.account_id, t.txn_timestamp, t.balance_after
FROM transactions t
JOIN account_status_history h ON h.account_id = t.account_id AND h.to_status = 'CLOSED'
WHERE t.txn_timestamp > h.event_timestamp
ORDER BY issue, transaction_id;
