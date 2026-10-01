-- VirtuWill data model · savings goals owned by Money
-- The Finance tracker is retired, so Money edits savings goals directly. A goal
-- has a dollar target and gets its progress one of two ways:
--   * linked to an account (a dedicated savings account): that account's
--     balance as it stands now (finance.current_balances), or
--   * not linked: the latest saved amount recorded for it
--     (finance.balance_snapshots with goal_id), as before.

ALTER TABLE finance.savings_goals ADD COLUMN IF NOT EXISTS account_id TEXT REFERENCES finance.accounts ON DELETE SET NULL;

DROP VIEW IF EXISTS finance.goal_progress;
CREATE VIEW finance.goal_progress AS
SELECT g.goal_id, g.name, g.target, g.contribution_per_check, g.due_on, al.name AS allocation,
       COALESCE(acct.as_of, b.as_of) AS as_of,
       COALESCE(acct.balance, b.balance) AS balance,
       CASE WHEN g.target > 0 AND COALESCE(acct.balance, b.balance) IS NOT NULL
            THEN ROUND(100 * COALESCE(acct.balance, b.balance) / g.target, 1) END AS pct_of_target,
       g.account_id, acct.account_name, acct.mask AS account_mask,
       CASE WHEN acct.balance IS NOT NULL THEN 'account' WHEN b.balance IS NOT NULL THEN 'saved' END AS balance_source,
       g.note
FROM finance.savings_goals g
LEFT JOIN finance.allocations al USING (allocation_id)
LEFT JOIN LATERAL (
    SELECT as_of, balance FROM finance.balance_snapshots s
    WHERE s.goal_id = g.goal_id ORDER BY as_of DESC, snapshot_id DESC LIMIT 1
) b ON true
LEFT JOIN LATERAL (
    -- The account now: its estimate when what posted since is complete, else its last known balance.
    SELECT c.name AS account_name, c.mask,
           CASE WHEN c.transactions_since > 0 AND c.estimate_complete THEN c.last_posted ELSE c.as_of END AS as_of,
           CASE WHEN c.estimate_complete THEN c.estimated_balance ELSE c.balance END AS balance
    FROM finance.current_balances c WHERE c.account_id = g.account_id
) acct ON true;
