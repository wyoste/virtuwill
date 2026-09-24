-- VirtuWill data model · finance
-- Accounts, bank activity, grocery receipts, budgets, recurring bills, pay,
-- allocations, savings goals, balances and retirement.
--
-- Money is NUMERIC(12,2) in US dollars. Transaction amounts keep the bank's
-- sign: positive is money leaving the account (a charge, a transfer out, a card
-- payment); negative is money coming in (pay, interest, a refund).

CREATE SCHEMA IF NOT EXISTS finance;

-- ── Accounts ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS finance.accounts (
    account_id TEXT PRIMARY KEY,            -- e.g. 'bank-checking-1234', 'card-5678', 'roth-ira'
    institution TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    mask TEXT,                              -- last four digits
    account_type TEXT NOT NULL CHECK (account_type IN
        ('checking', 'savings', 'credit_card', 'brokerage', 'retirement', 'cash', 'loan', 'other')),
    retirement_type TEXT CHECK (retirement_type IN ('roth_ira', 'traditional_ira', '401k', '403b', '401a')),
    purpose TEXT NOT NULL DEFAULT '',       -- Household, Travel, Dogs …
    is_active BOOLEAN NOT NULL DEFAULT true,
    show_in_journal BOOLEAN NOT NULL DEFAULT false,   -- the journal's account template
    journal_position INTEGER,
    CHECK ((account_type = 'retirement') = (retirement_type IS NOT NULL))
);

-- Every way a source names an account: "Bank 1234", "VISA 1234", "Household · 1234".
CREATE TABLE IF NOT EXISTS finance.account_aliases (
    alias TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES finance.accounts ON DELETE CASCADE
);

-- ── Categories ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS finance.categories (
    category TEXT PRIMARY KEY,              -- Dining, Subscriptions, Groceries, Review …
    category_group TEXT,                    -- e.g. 'Discretionary'
    is_spending BOOLEAN NOT NULL DEFAULT true,
    needs_review BOOLEAN NOT NULL DEFAULT false
);
INSERT INTO finance.categories (category, needs_review) VALUES ('Review', true), ('Groceries', false)
ON CONFLICT (category) DO NOTHING;

-- What a non-spending movement is: it never counts as spending.
CREATE TABLE IF NOT EXISTS finance.movement_types (
    movement_type TEXT PRIMARY KEY,
    flow TEXT NOT NULL CHECK (flow IN ('income', 'transfer', 'card_payment', 'unconfirmed')),
    confirmed BOOLEAN NOT NULL DEFAULT true
);
INSERT INTO finance.movement_types VALUES
    ('Payroll deposit', 'income', true),
    ('Interest income', 'income', true),
    ('Internal transfer', 'transfer', true),
    ('Credit card payment', 'card_payment', true),
    ('Robinhood transfer - purpose unconfirmed', 'unconfirmed', false),
    ('Fidelity transfer - purpose unconfirmed', 'unconfirmed', false),
    ('Venmo - purpose unconfirmed', 'unconfirmed', false),
    ('Zelle - purpose unconfirmed', 'unconfirmed', false),
    ('Employer credit - purpose unconfirmed', 'unconfirmed', false)
ON CONFLICT (movement_type) DO NOTHING;

-- ── Bank activity ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS finance.transactions (
    transaction_id TEXT PRIMARY KEY,        -- the statement's id, e.g. 'chase-TX001'
    posted_on DATE NOT NULL REFERENCES core.calendar (day),
    account_id TEXT REFERENCES finance.accounts,
    account_text TEXT NOT NULL,             -- as the source named it
    merchant TEXT NOT NULL DEFAULT '',
    amount NUMERIC(12,2) NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('expense', 'movement')),
    category TEXT REFERENCES finance.categories,
    movement_type TEXT REFERENCES finance.movement_types,
    classification TEXT,                    -- free annotation, e.g. 'Direct spending'
    source_document TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL CHECK (source IN ('finance_tracker', 'import', 'manual')),
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((kind = 'movement') = (movement_type IS NOT NULL)),
    CHECK (kind = 'movement' OR category IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS transactions_by_date ON finance.transactions (posted_on);
CREATE INDEX IF NOT EXISTS transactions_by_account ON finance.transactions (account_id, posted_on);

-- ── Grocery receipts ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS finance.receipts (
    receipt_id TEXT PRIMARY KEY,
    purchased_on DATE NOT NULL REFERENCES core.calendar (day),
    store TEXT NOT NULL,
    net NUMERIC(12,2) NOT NULL,
    tax NUMERIC(12,2) NOT NULL DEFAULT 0,
    total NUMERIC(12,2) NOT NULL,
    savings NUMERIC(12,2) NOT NULL DEFAULT 0,
    payment_text TEXT NOT NULL DEFAULT '',
    account_id TEXT REFERENCES finance.accounts,
    -- The bank charge this receipt explains (same amount within a cent, within 3 days).
    -- A matched receipt adds detail, never a second expense.
    matched_transaction_id TEXT UNIQUE REFERENCES finance.transactions ON DELETE SET NULL,
    source TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    CHECK (abs(net + tax - total) <= 0.01)
);
CREATE INDEX IF NOT EXISTS receipts_by_date ON finance.receipts (purchased_on);

CREATE TABLE IF NOT EXISTS finance.grocery_categories (
    grocery_category TEXT PRIMARY KEY       -- Produce, Salads, Dairy …
);
-- How an item name is categorized; new lines inherit it.
CREATE TABLE IF NOT EXISTS finance.grocery_catalog (
    item_name TEXT PRIMARY KEY,
    grocery_category TEXT NOT NULL REFERENCES finance.grocery_categories,
    food_id TEXT REFERENCES health.foods ON DELETE SET NULL   -- nutrition reference, once linked
);

CREATE TABLE IF NOT EXISTS finance.receipt_items (
    receipt_id TEXT NOT NULL REFERENCES finance.receipts ON DELETE CASCADE,
    line INTEGER NOT NULL CHECK (line > 0),
    item_name TEXT NOT NULL,
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    unit TEXT NOT NULL DEFAULT 'ea',
    amount NUMERIC(12,2) NOT NULL,
    discount NUMERIC(12,2) NOT NULL DEFAULT 0,
    grocery_category TEXT NOT NULL REFERENCES finance.grocery_categories,
    planned TEXT NOT NULL DEFAULT 'Unknown' CHECK (planned IN ('Planned', 'Unplanned', 'Unknown')),
    PRIMARY KEY (receipt_id, line)
);

CREATE TABLE IF NOT EXISTS finance.shopping_list (
    item_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    quantity_text TEXT NOT NULL DEFAULT '',
    price_cap NUMERIC(12,2),
    note TEXT NOT NULL DEFAULT '',
    to_buy BOOLEAN NOT NULL DEFAULT true,
    done BOOLEAN NOT NULL DEFAULT false
);

-- ── Plan: budgets, recurring bills, pay and allocations ──────────────────────
CREATE TABLE IF NOT EXISTS finance.budgets (
    budget_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    basis TEXT NOT NULL CHECK (basis IN ('fixed', 'per_paycheck', 'recurring_expenses', 'unset')),
    amount_monthly NUMERIC(12,2),           -- basis = fixed
    per_paycheck NUMERIC(12,2),             -- basis = per_paycheck
    paychecks_per_year SMALLINT,
    note TEXT NOT NULL DEFAULT '',
    CHECK (basis <> 'fixed' OR amount_monthly IS NOT NULL),
    CHECK (basis <> 'per_paycheck' OR (per_paycheck IS NOT NULL AND paychecks_per_year > 0))
);
-- A category belongs to at most one budget; a budget can pool several.
CREATE TABLE IF NOT EXISTS finance.budget_categories (
    budget_id TEXT NOT NULL REFERENCES finance.budgets ON DELETE CASCADE,
    category TEXT NOT NULL UNIQUE REFERENCES finance.categories,
    PRIMARY KEY (budget_id, category)
);

CREATE TABLE IF NOT EXISTS finance.recurring_expenses (
    expense_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    amount NUMERIC(12,2),                   -- NULL when the price isn't known
    frequency TEXT NOT NULL CHECK (frequency IN ('Weekly', 'Biweekly', 'Monthly', 'Annual')),
    due_day SMALLINT CHECK (due_day BETWEEN 1 AND 31),
    status TEXT NOT NULL CHECK (status IN ('Active', 'Unconfirmed', 'Canceled', 'Expiring')),
    category TEXT NOT NULL REFERENCES finance.categories,
    account_id TEXT REFERENCES finance.accounts,
    note TEXT NOT NULL DEFAULT ''
);

-- Paycheck facts as of a pay date; the latest row is current.
CREATE TABLE IF NOT EXISTS finance.pay_profile (
    as_of DATE PRIMARY KEY REFERENCES core.calendar (day),
    net_pay NUMERIC(12,2) NOT NULL,
    gross_pay NUMERIC(12,2) NOT NULL,
    checks_per_year SMALLINT NOT NULL CHECK (checks_per_year > 0),
    anchor_date DATE NOT NULL,              -- a known pay date; periods repeat every 14 days
    gross_ytd NUMERIC(12,2),
    net_ytd NUMERIC(12,2),
    bonus_ytd NUMERIC(12,2)
);

-- How each paycheck is split across accounts.
CREATE TABLE IF NOT EXISTS finance.paycheck_deposits (
    position SMALLINT PRIMARY KEY,
    destination_text TEXT NOT NULL,
    account_id TEXT REFERENCES finance.accounts,
    amount NUMERIC(12,2) NOT NULL CHECK (amount >= 0)
);

CREATE TABLE IF NOT EXISTS finance.other_incomes (
    income_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    frequency TEXT NOT NULL CHECK (frequency IN ('Weekly', 'Biweekly', 'Monthly', 'Annual')),
    note TEXT NOT NULL DEFAULT ''
);

-- Per-paycheck plan for where money goes.
CREATE TABLE IF NOT EXISTS finance.allocations (
    allocation_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    amount NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
    cadence TEXT NOT NULL CHECK (cadence IN ('Weekly', 'Biweekly', 'Monthly', 'Annual')),
    account_id TEXT REFERENCES finance.accounts,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS finance.savings_goals (
    goal_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    target NUMERIC(12,2),
    contribution_per_check NUMERIC(12,2) NOT NULL DEFAULT 0,
    due_on DATE,
    allocation_id TEXT REFERENCES finance.allocations ON DELETE SET NULL,
    note TEXT NOT NULL DEFAULT ''
);

-- ── Balances and retirement ──────────────────────────────────────────────────
-- Observed balances over time: account statements, retirement portals, savings
-- buckets, and the account list in each journal entry.
CREATE TABLE IF NOT EXISTS finance.balance_snapshots (
    snapshot_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    as_of DATE NOT NULL REFERENCES core.calendar (day),
    account_id TEXT REFERENCES finance.accounts,
    goal_id TEXT REFERENCES finance.savings_goals ON DELETE CASCADE,
    balance NUMERIC(14,2),                  -- NULL when the source had a blank balance
    institution_text TEXT NOT NULL DEFAULT '',
    account_text TEXT NOT NULL DEFAULT '',
    position SMALLINT,                      -- order within a journal entry
    source TEXT NOT NULL CHECK (source IN ('finance_tracker', 'journal', 'manual')),
    source_ref TEXT,                        -- e.g. the journal entry id
    CHECK (account_id IS NOT NULL OR goal_id IS NOT NULL OR account_text <> '' OR institution_text <> '')
);
CREATE INDEX IF NOT EXISTS snapshots_by_account ON finance.balance_snapshots (account_id, as_of);
CREATE INDEX IF NOT EXISTS snapshots_by_goal ON finance.balance_snapshots (goal_id, as_of);
CREATE INDEX IF NOT EXISTS snapshots_by_source ON finance.balance_snapshots (source, source_ref);

CREATE TABLE IF NOT EXISTS finance.retirement_plan (
    as_of DATE PRIMARY KEY REFERENCES core.calendar (day),
    employee_per_check NUMERIC(12,2),
    employer_per_check NUMERIC(12,2),
    employee_ytd NUMERIC(12,2),
    employer_ytd NUMERIC(12,2),
    deferral_limit NUMERIC(12,2),
    other_deferrals NUMERIC(12,2),
    remaining_checks SMALLINT,
    ira_per_check NUMERIC(12,2),
    ira_limit NUMERIC(12,2),
    ira_actual NUMERIC(12,2)
);

-- ── Views ────────────────────────────────────────────────────────────────────
DROP VIEW IF EXISTS finance.spending, finance.recurring_monthly, finance.effective_budgets,
                    finance.budget_vs_actual, finance.monthly_spending, finance.pay_periods,
                    finance.pay_period_cash_flow, finance.receipt_reconciliation,
                    finance.grocery_spending, finance.latest_balances, finance.goal_progress,
                    finance.retirement_summary CASCADE;

-- Every expense once: bank charges, with matched receipts folded in as Groceries,
-- plus receipts that no bank charge explains yet.
CREATE VIEW finance.spending AS
SELECT t.transaction_id AS spend_id, t.posted_on AS day, t.account_id, t.merchant,
       CASE WHEN r.receipt_id IS NOT NULL THEN 'Groceries' ELSE t.category END AS category,
       t.amount, 'bank'::text AS origin, r.receipt_id
FROM finance.transactions t
LEFT JOIN finance.receipts r ON r.matched_transaction_id = t.transaction_id
WHERE t.kind = 'expense'
UNION ALL
SELECT 'receipt-' || r.receipt_id, r.purchased_on, r.account_id, r.store, 'Groceries', r.total, 'receipt', r.receipt_id
FROM finance.receipts r
WHERE r.matched_transaction_id IS NULL;

CREATE VIEW finance.recurring_monthly AS
SELECT e.*,
       CASE WHEN e.amount IS NULL THEN 0
            ELSE e.amount * CASE e.frequency WHEN 'Weekly' THEN 52 / 12.0 WHEN 'Biweekly' THEN 26 / 12.0
                                             WHEN 'Annual' THEN 1 / 12.0 ELSE 1 END END AS monthly_amount,
       e.status IN ('Active', 'Unconfirmed') AS is_current
FROM finance.recurring_expenses e;

CREATE VIEW finance.effective_budgets AS
SELECT b.budget_id, b.name, b.basis, b.note,
       ROUND(CASE b.basis
           WHEN 'fixed' THEN b.amount_monthly
           WHEN 'per_paycheck' THEN b.per_paycheck * b.paychecks_per_year / 12.0
           WHEN 'recurring_expenses' THEN (
               SELECT COALESCE(SUM(r.monthly_amount), 0) FROM finance.recurring_monthly r
               JOIN finance.budget_categories bc ON bc.category = r.category
               WHERE bc.budget_id = b.budget_id AND r.is_current)
       END, 2) AS monthly_amount
FROM finance.budgets b;

CREATE VIEW finance.monthly_spending AS
SELECT c.month_start, s.category, cat.category_group, bc.budget_id,
       SUM(s.amount) AS amount, COUNT(*) AS transactions
FROM finance.spending s
JOIN core.calendar c ON c.day = s.day
LEFT JOIN finance.categories cat ON cat.category = s.category
LEFT JOIN finance.budget_categories bc ON bc.category = s.category
GROUP BY c.month_start, s.category, cat.category_group, bc.budget_id;

CREATE VIEW finance.budget_vs_actual AS
WITH months AS (SELECT DISTINCT month_start FROM finance.monthly_spending)
SELECT m.month_start, b.budget_id, b.name, b.monthly_amount AS budget,
       COALESCE(SUM(ms.amount), 0) AS actual,
       b.monthly_amount - COALESCE(SUM(ms.amount), 0) AS remaining,
       b.monthly_amount IS NOT NULL AND COALESCE(SUM(ms.amount), 0) > b.monthly_amount AS over_budget
FROM months m
CROSS JOIN finance.effective_budgets b
LEFT JOIN finance.monthly_spending ms ON ms.month_start = m.month_start AND ms.budget_id = b.budget_id
GROUP BY m.month_start, b.budget_id, b.name, b.monthly_amount;

-- Two-week pay periods from the latest pay profile's anchor date.
CREATE VIEW finance.pay_periods AS
WITH anchor AS (SELECT anchor_date FROM finance.pay_profile ORDER BY as_of DESC LIMIT 1)
SELECT c.day,
       a.anchor_date + (14 * floor((c.day - a.anchor_date) / 14.0))::int AS period_start
FROM core.calendar c CROSS JOIN anchor a;

CREATE VIEW finance.pay_period_cash_flow AS
WITH moves AS (
    SELECT p.period_start,
           SUM(-t.amount) FILTER (WHERE mt.flow = 'income') AS income_received,
           SUM(t.amount) FILTER (WHERE mt.flow = 'transfer' AND t.amount > 0) AS transferred_out,
           SUM(-t.amount) FILTER (WHERE mt.flow = 'transfer' AND t.amount < 0) AS transferred_in,
           SUM(t.amount) FILTER (WHERE mt.flow = 'card_payment') AS card_payments,
           SUM(abs(t.amount)) FILTER (WHERE mt.flow = 'unconfirmed') AS unconfirmed_movements
    FROM finance.transactions t
    JOIN finance.movement_types mt USING (movement_type)
    JOIN finance.pay_periods p ON p.day = t.posted_on
    GROUP BY p.period_start
), spend AS (
    SELECT p.period_start, SUM(s.amount) AS spending
    FROM finance.spending s JOIN finance.pay_periods p ON p.day = s.day
    GROUP BY p.period_start
)
SELECT period_start, period_start + 13 AS period_end,
       COALESCE(m.income_received, 0) AS income_received,
       COALESCE(s.spending, 0) AS spending,
       COALESCE(m.transferred_out, 0) AS transferred_out,
       COALESCE(m.transferred_in, 0) AS transferred_in,
       COALESCE(m.card_payments, 0) AS card_payments,
       COALESCE(m.unconfirmed_movements, 0) AS unconfirmed_movements
FROM moves m FULL JOIN spend s USING (period_start);

CREATE VIEW finance.receipt_reconciliation AS
SELECT r.receipt_id, r.purchased_on, r.store, r.net, r.tax, r.total,
       COALESCE(SUM(i.amount), 0) AS line_total,
       COUNT(i.line) AS lines,
       r.net - COALESCE(SUM(i.amount), 0) AS lines_vs_net,
       r.matched_transaction_id,
       t.amount AS bank_amount,
       t.amount - r.total AS bank_vs_total
FROM finance.receipts r
LEFT JOIN finance.receipt_items i USING (receipt_id)
LEFT JOIN finance.transactions t ON t.transaction_id = r.matched_transaction_id
GROUP BY r.receipt_id, t.amount;

CREATE VIEW finance.grocery_spending AS
SELECT c.month_start, i.grocery_category, SUM(i.amount) AS amount, SUM(i.discount) AS discounts,
       COUNT(*) AS lines, COUNT(*) FILTER (WHERE i.planned = 'Unplanned') AS unplanned_lines
FROM finance.receipt_items i
JOIN finance.receipts r USING (receipt_id)
JOIN core.calendar c ON c.day = r.purchased_on
GROUP BY c.month_start, i.grocery_category;

CREATE VIEW finance.latest_balances AS
SELECT DISTINCT ON (s.account_id)
       s.account_id, a.name, a.institution, a.account_type, a.retirement_type, s.as_of, s.balance, s.source
FROM finance.balance_snapshots s
JOIN finance.accounts a USING (account_id)
WHERE s.balance IS NOT NULL
ORDER BY s.account_id, s.as_of DESC, s.snapshot_id DESC;

CREATE VIEW finance.goal_progress AS
SELECT g.goal_id, g.name, g.target, g.contribution_per_check, g.due_on, al.name AS allocation,
       b.as_of, b.balance,
       CASE WHEN g.target > 0 AND b.balance IS NOT NULL THEN ROUND(100 * b.balance / g.target, 1) END AS pct_of_target
FROM finance.savings_goals g
LEFT JOIN finance.allocations al USING (allocation_id)
LEFT JOIN LATERAL (
    SELECT as_of, balance FROM finance.balance_snapshots s
    WHERE s.goal_id = g.goal_id ORDER BY as_of DESC, snapshot_id DESC LIMIT 1
) b ON true;

CREATE VIEW finance.retirement_summary AS
WITH plan AS (SELECT * FROM finance.retirement_plan ORDER BY as_of DESC LIMIT 1)
SELECT (SELECT SUM(balance) FROM finance.latest_balances WHERE account_type = 'retirement') AS total_balance,
       (SELECT MAX(as_of) FROM finance.latest_balances WHERE account_type = 'retirement') AS balances_as_of,
       p.as_of AS plan_as_of, p.employee_ytd, p.employer_ytd, p.deferral_limit,
       p.deferral_limit - p.employee_ytd - COALESCE(p.other_deferrals, 0) AS deferral_room,
       p.employee_per_check * p.remaining_checks AS projected_remaining_deferrals,
       p.ira_limit, p.ira_per_check, p.ira_actual
FROM plan p;
