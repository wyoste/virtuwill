-- VirtuWill data model · finance imports, paychecks, and today's balances
-- Files exported from bank, card, payroll and store portals are extracted into
-- a structured finance bundle (virtuwill/importers), staged for review, then
-- committed into the finance tables. Imported rows own their data: the Finance
-- tracker's copy of the same bank transaction or receipt is taken over, and the
-- tracker no longer re-creates it.

-- ── Staged imports ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS finance.staged_imports (
    import_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,                   -- of the source file(s); a committed file is never loaded twice
    parser TEXT NOT NULL,
    bundle JSONB NOT NULL,                  -- the extracted records
    preview JSONB NOT NULL,                 -- what committing would do: new, already loaded, taken over
    status TEXT NOT NULL DEFAULT 'staged' CHECK (status IN ('staged', 'committed', 'discarded')),
    report JSONB,                           -- what committing did
    document_id BIGINT REFERENCES finance.source_documents ON DELETE SET NULL,
    raw_asset_ids BIGINT[] NOT NULL DEFAULT '{}',   -- the original files, kept privately
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    committed_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS one_committed_import_per_file ON finance.staged_imports (sha256) WHERE status = 'committed';

-- ── Paychecks (actuals, from pay statements) ─────────────────────────────────
-- finance.pay_profile and paycheck_deposits hold the *plan*; these are what was paid.
CREATE TABLE IF NOT EXISTS finance.paychecks (
    paycheck_id TEXT PRIMARY KEY,           -- 'adv-<deposit advice number>'
    pay_date DATE NOT NULL REFERENCES core.calendar (day),
    period_start DATE REFERENCES core.calendar (day),
    period_end DATE REFERENCES core.calendar (day),
    employer TEXT NOT NULL DEFAULT '',
    gross NUMERIC(12,2) NOT NULL,
    pre_tax NUMERIC(12,2) NOT NULL DEFAULT 0,
    post_tax NUMERIC(12,2) NOT NULL DEFAULT 0,
    taxes NUMERIC(12,2) NOT NULL DEFAULT 0,
    net NUMERIC(12,2) NOT NULL,
    gross_ytd NUMERIC(12,2),
    net_ytd NUMERIC(12,2),
    document_id BIGINT REFERENCES finance.source_documents ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'import' CHECK (source IN ('import', 'manual'))
);
CREATE INDEX IF NOT EXISTS paychecks_by_date ON finance.paychecks (pay_date);

CREATE TABLE IF NOT EXISTS finance.paycheck_lines (
    paycheck_id TEXT NOT NULL REFERENCES finance.paychecks ON DELETE CASCADE,
    position INTEGER NOT NULL,
    section TEXT NOT NULL CHECK (section IN ('earnings', 'taxable_benefits', 'memo', 'pre_tax', 'post_tax', 'taxes')),
    name TEXT NOT NULL,                     -- 'Regular', '401K', 'Fed W/H', '401K ER Match' …
    hours NUMERIC,
    rate NUMERIC,
    current_amount NUMERIC(12,2),           -- NULL when the line has only a year-to-date amount
    ytd_hours NUMERIC,
    ytd_amount NUMERIC(12,2),
    PRIMARY KEY (paycheck_id, position)
);

-- Where each paycheck's net pay went (account last four only; no routing numbers).
CREATE TABLE IF NOT EXISTS finance.paycheck_splits (
    paycheck_id TEXT NOT NULL REFERENCES finance.paychecks ON DELETE CASCADE,
    position INTEGER NOT NULL,
    account_id TEXT REFERENCES finance.accounts,
    account_mask TEXT NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    PRIMARY KEY (paycheck_id, position)
);

-- ── Receipt line detail from store exports ──────────────────────────────────
ALTER TABLE finance.receipt_items ADD COLUMN IF NOT EXISTS regular_price NUMERIC(12,2);
ALTER TABLE finance.receipt_items ADD COLUMN IF NOT EXISTS store_brand BOOLEAN;
ALTER TABLE finance.receipt_items ADD COLUMN IF NOT EXISTS weighed BOOLEAN;

-- ── Views for Today and the Money dashboard ──────────────────────────────────
-- Spending per calendar day (bank charges, plus receipt amounts no charge explains).
CREATE VIEW finance.daily_spending AS
SELECT day, SUM(amount) AS amount, COUNT(*) AS transactions
FROM finance.spending
GROUP BY day;

-- Each account now: its latest known balance, plus what has posted since.
-- For an account owed (a card), charges raise the balance; for cash accounts
-- they lower it. The estimate is only trusted (estimate_complete) when charges
-- and payments/deposits both posted since the balance was known, or nothing
-- moved: a spending-only export lists purchases but not the payments that
-- offset them.
CREATE VIEW finance.current_balances AS
SELECT a.account_id, a.name, a.institution, a.mask, a.account_type, t.is_liability, a.is_active,
       l.as_of, l.balance, l.balance_kind, l.source AS balance_source,
       COALESCE(x.activity, 0) AS activity_since,
       COALESCE(x.outflows, 0) AS outflows_since,
       COALESCE(x.inflows, 0) AS inflows_since,
       COALESCE(x.transactions, 0) AS transactions_since,
       x.last_posted,
       CASE WHEN l.balance IS NULL THEN NULL
            WHEN t.is_liability THEN l.balance + COALESCE(x.activity, 0)
            ELSE l.balance - COALESCE(x.activity, 0) END AS estimated_balance,
       COALESCE(x.transactions, 0) = 0 OR (COALESCE(x.outflows, 0) > 0 AND COALESCE(x.inflows, 0) > 0) AS estimate_complete
FROM finance.accounts a
JOIN finance.account_types t USING (account_type)
LEFT JOIN finance.latest_balances l USING (account_id)
LEFT JOIN LATERAL (
    SELECT SUM(tx.amount) AS activity, SUM(tx.amount) FILTER (WHERE tx.amount > 0) AS outflows,
           -- payments and deposits, not refunds: a refund doesn't show the payments are loaded
           -SUM(tx.amount) FILTER (WHERE tx.amount < 0 AND tx.kind = 'movement') AS inflows,
           COUNT(*) AS transactions, MAX(tx.posted_on) AS last_posted
    FROM finance.transactions tx
    WHERE tx.account_id = a.account_id AND (l.as_of IS NULL OR tx.posted_on > l.as_of)
) x ON true;

-- Pay by month, from pay statements.
CREATE VIEW finance.monthly_pay AS
SELECT date_trunc('month', pay_date)::date AS month_start, COUNT(*) AS paychecks,
       SUM(gross) AS gross, SUM(pre_tax) AS pre_tax, SUM(taxes) AS taxes, SUM(post_tax) AS post_tax, SUM(net) AS net
FROM finance.paychecks
GROUP BY 1;

-- Budgets against spending, per month. Spending is summed once per month and
-- budget first; joining the spending view directly re-ran it for every budget.
CREATE OR REPLACE VIEW finance.budget_vs_actual AS
WITH spent AS MATERIALIZED (
    SELECT month_start, budget_id, SUM(amount) AS amount FROM finance.monthly_spending GROUP BY 1, 2
), months AS (
    SELECT DISTINCT month_start FROM spent
)
SELECT m.month_start, b.budget_id, b.name, b.monthly_amount AS budget,
       COALESCE(s.amount, 0) AS actual,
       b.monthly_amount - COALESCE(s.amount, 0) AS remaining,
       b.monthly_amount IS NOT NULL AND COALESCE(s.amount, 0) > b.monthly_amount AS over_budget
FROM months m
CROSS JOIN finance.effective_budgets b
LEFT JOIN spent s ON s.month_start = m.month_start AND s.budget_id = b.budget_id;
