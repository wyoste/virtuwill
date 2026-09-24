-- VirtuWill data model · finance
-- Accounts, source documents (statement PDFs, CSV exports, receipts), bank
-- activity, point-in-time balances, receipts with line items tied to the bank
-- charges they explain, budgets, recurring bills, pay, allocations, savings
-- goals and retirement.
--
-- Money is NUMERIC(12,2) in US dollars. Transaction amounts keep the bank's
-- sign: positive is money leaving the account (a charge, a transfer out, a card
-- payment); negative is money coming in (pay, interest, a refund). Balances are
-- what the statement shows: cash on hand for deposit accounts, the amount owed
-- for credit cards and loans.

CREATE SCHEMA IF NOT EXISTS finance;

-- ── Accounts ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS finance.account_types (
    account_type TEXT PRIMARY KEY,
    is_liability BOOLEAN NOT NULL           -- balance is an amount owed; charges raise it
);
INSERT INTO finance.account_types VALUES
    ('checking', false), ('savings', false), ('brokerage', false), ('retirement', false), ('cash', false),
    ('credit_card', true), ('loan', true), ('other', false)
ON CONFLICT (account_type) DO NOTHING;

CREATE TABLE IF NOT EXISTS finance.accounts (
    account_id TEXT PRIMARY KEY,            -- e.g. 'bank-checking-1234', 'card-5678', 'roth-ira'
    institution TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    mask TEXT,                              -- last four digits
    account_type TEXT NOT NULL REFERENCES finance.account_types,
    retirement_type TEXT CHECK (retirement_type IN ('roth_ira', 'traditional_ira', '401k', '403b', '401a')),
    purpose TEXT NOT NULL DEFAULT '',       -- Household, Travel, Dogs …
    opened_on DATE,
    closed_on DATE,
    is_active BOOLEAN NOT NULL DEFAULT true,
    show_in_journal BOOLEAN NOT NULL DEFAULT false,   -- the journal's account template
    journal_position INTEGER,
    CHECK ((account_type = 'retirement') = (retirement_type IS NOT NULL))
);

-- Every way a source names an account: "Bank 1234", "VISA 1234", "Household · 1234",
-- a card number printed on a receipt, or an account name in a CSV export.
CREATE TABLE IF NOT EXISTS finance.account_aliases (
    alias TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES finance.accounts ON DELETE CASCADE
);

-- ── Source documents ─────────────────────────────────────────────────────────
-- How to read one institution's files: which CSV columns hold what, the sign
-- convention, and the file-name pattern that identifies them in a folder.
CREATE TABLE IF NOT EXISTS finance.import_profiles (
    profile_id TEXT PRIMARY KEY,            -- e.g. 'bank-checking-csv', 'card-statement-pdf'
    institution TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    file_format TEXT NOT NULL,
    filename_pattern TEXT NOT NULL DEFAULT '',   -- regular expression
    account_id TEXT REFERENCES finance.accounts,  -- when every file is for one account
    column_map JSONB NOT NULL DEFAULT '{}', -- {"date": "Posting Date", "amount": "Amount", "balance": "Balance", …}
    amount_sign TEXT NOT NULL DEFAULT 'outflow_positive'
        CHECK (amount_sign IN ('outflow_positive', 'inflow_positive', 'split_columns')),
    date_format TEXT NOT NULL DEFAULT 'YYYY-MM-DD',
    parser TEXT NOT NULL DEFAULT 'csv',     -- 'csv', or the PDF statement parser's name
    notes TEXT NOT NULL DEFAULT ''
);

-- One run over a folder of files.
CREATE TABLE IF NOT EXISTS finance.import_batches (
    batch_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_folder TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    files_seen INTEGER NOT NULL DEFAULT 0,
    files_new INTEGER NOT NULL DEFAULT 0,
    files_failed INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);

-- Every statement, export and receipt file, registered once by content hash.
-- The file itself is a private media asset; re-importing the same file is a no-op.
CREATE TABLE IF NOT EXISTS finance.source_documents (
    document_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    original_filename TEXT NOT NULL,
    source_folder TEXT NOT NULL DEFAULT '',
    sha256 TEXT UNIQUE,                     -- NULL only for files known by name but not yet provided
    asset_id BIGINT REFERENCES core.media_assets ON DELETE SET NULL,
    doc_type TEXT NOT NULL CHECK (doc_type IN
        ('statement', 'transaction_export', 'receipt', 'retirement_statement', 'pay_stub', 'tax_form', 'other')),
    file_format TEXT NOT NULL CHECK (file_format IN ('pdf', 'csv', 'ofx', 'qfx', 'xlsx', 'image', 'html', 'email', 'other')),
    institution TEXT NOT NULL DEFAULT '',
    account_id TEXT REFERENCES finance.accounts,
    period_start DATE REFERENCES core.calendar (day),
    period_end DATE REFERENCES core.calendar (day),
    document_date DATE REFERENCES core.calendar (day),   -- statement or receipt date
    import_profile_id TEXT REFERENCES finance.import_profiles,
    import_batch_id BIGINT REFERENCES finance.import_batches ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'registered'
        CHECK (status IN ('registered', 'parsed', 'needs_review', 'failed', 'ignored')),
    parsed_rows INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_end IS NULL OR period_start IS NULL OR period_end >= period_start)
);
CREATE INDEX IF NOT EXISTS documents_by_account ON finance.source_documents (account_id, period_start, period_end);
CREATE UNIQUE INDEX IF NOT EXISTS documents_by_name_when_unhashed
    ON finance.source_documents (source_folder, original_filename) WHERE sha256 IS NULL;

-- The balances and totals printed on a statement.
CREATE TABLE IF NOT EXISTS finance.statements (
    statement_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL UNIQUE REFERENCES finance.source_documents ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES finance.accounts,
    period_start DATE NOT NULL REFERENCES core.calendar (day),
    period_end DATE NOT NULL REFERENCES core.calendar (day),
    opening_balance NUMERIC(14,2),
    closing_balance NUMERIC(14,2),
    total_debits NUMERIC(14,2),             -- money out, as printed
    total_credits NUMERIC(14,2),            -- money in, as printed
    interest_charged NUMERIC(12,2),
    fees_charged NUMERIC(12,2),
    minimum_due NUMERIC(12,2),
    payment_due_on DATE,
    credit_limit NUMERIC(14,2),
    UNIQUE (account_id, period_end),
    CHECK (period_end >= period_start)
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
    ('Employer credit - purpose unconfirmed', 'unconfirmed', false),
    ('Retirement contribution', 'transfer', true),
    ('Employer contribution', 'income', true),
    ('Dividend', 'income', true),
    ('Refund', 'income', true)
ON CONFLICT (movement_type) DO NOTHING;

-- ── Bank activity ────────────────────────────────────────────────────────────
-- One row per real transaction. The same transaction often appears in more
-- than one file (the CSV export and the PDF statement); it is stored once and
-- each appearance is recorded in transaction_sources.
CREATE TABLE IF NOT EXISTS finance.transactions (
    transaction_id TEXT PRIMARY KEY,        -- the bank's id when it has one, else '<account>:<fingerprint>'
    account_id TEXT REFERENCES finance.accounts,
    account_text TEXT NOT NULL,             -- as the source named it
    posted_on DATE NOT NULL REFERENCES core.calendar (day),
    transacted_on DATE REFERENCES core.calendar (day),   -- purchase or authorization date, when shown
    description_raw TEXT NOT NULL DEFAULT '',            -- exactly as the bank printed it
    merchant TEXT NOT NULL DEFAULT '',                   -- cleaned name
    amount NUMERIC(12,2) NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    kind TEXT NOT NULL CHECK (kind IN ('expense', 'movement')),
    category TEXT REFERENCES finance.categories,
    movement_type TEXT REFERENCES finance.movement_types,
    classification TEXT,                    -- free annotation, e.g. 'Direct spending'
    check_number TEXT,
    external_id TEXT,                       -- the bank's transaction id, if exported
    fingerprint TEXT NOT NULL,              -- hash of account, date, amount, description and same-day order
    is_pending BOOLEAN NOT NULL DEFAULT false,
    source TEXT NOT NULL CHECK (source IN ('finance_tracker', 'import', 'manual')),
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, fingerprint),
    UNIQUE (account_id, external_id),
    CHECK ((kind = 'movement') = (movement_type IS NOT NULL)),
    CHECK (kind = 'movement' OR category IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS transactions_by_date ON finance.transactions (posted_on);
CREATE INDEX IF NOT EXISTS transactions_by_account ON finance.transactions (account_id, posted_on);

-- Where each transaction was seen: file, page or row, and the running balance
-- the file printed beside it.
CREATE TABLE IF NOT EXISTS finance.transaction_sources (
    transaction_id TEXT NOT NULL REFERENCES finance.transactions ON DELETE CASCADE,
    document_id BIGINT NOT NULL REFERENCES finance.source_documents ON DELETE CASCADE,
    source_row TEXT NOT NULL DEFAULT '',    -- 'p. 5', 'row 42'
    reported_balance NUMERIC(14,2),
    raw JSONB NOT NULL DEFAULT '{}',        -- the row as parsed
    PRIMARY KEY (transaction_id, document_id, source_row)
);
CREATE INDEX IF NOT EXISTS transaction_sources_by_document ON finance.transaction_sources (document_id);

-- ── Receipts ─────────────────────────────────────────────────────────────────
-- Any store: groceries, hardware, online orders, restaurants.
CREATE TABLE IF NOT EXISTS finance.receipts (
    receipt_id TEXT PRIMARY KEY,
    purchased_on DATE NOT NULL REFERENCES core.calendar (day),
    purchased_at TIMESTAMPTZ,
    merchant TEXT NOT NULL,                 -- 'Kroger'
    store_location TEXT NOT NULL DEFAULT '',-- '2925 Custer Rd'
    category TEXT NOT NULL DEFAULT 'Groceries' REFERENCES finance.categories,   -- spending category of the purchase
    net NUMERIC(12,2) NOT NULL,             -- sum of lines after line discounts
    tax NUMERIC(12,2) NOT NULL DEFAULT 0,
    tip NUMERIC(12,2) NOT NULL DEFAULT 0,
    total NUMERIC(12,2) NOT NULL,           -- negative for a return
    savings NUMERIC(12,2) NOT NULL DEFAULT 0,
    payment_text TEXT NOT NULL DEFAULT '',  -- tender line(s) as printed
    order_number TEXT,
    document_id BIGINT REFERENCES finance.source_documents ON DELETE SET NULL,
    source TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    CHECK (abs(net + tax + tip - total) <= 0.01)
);
CREATE INDEX IF NOT EXISTS receipts_by_date ON finance.receipts (purchased_on);

-- Item categories (Produce, Dairy, Hardware …) roll up to a spending category.
CREATE TABLE IF NOT EXISTS finance.item_categories (
    item_category TEXT PRIMARY KEY,
    spending_category TEXT REFERENCES finance.categories
);
-- How an item name is categorized; new lines inherit it.
CREATE TABLE IF NOT EXISTS finance.item_catalog (
    item_name TEXT PRIMARY KEY,
    item_category TEXT NOT NULL REFERENCES finance.item_categories,
    food_id TEXT REFERENCES health.foods ON DELETE SET NULL   -- nutrition reference, once linked
);

CREATE TABLE IF NOT EXISTS finance.receipt_items (
    receipt_id TEXT NOT NULL REFERENCES finance.receipts ON DELETE CASCADE,
    line INTEGER NOT NULL CHECK (line > 0),
    item_name TEXT NOT NULL,
    sku TEXT,
    quantity NUMERIC NOT NULL CHECK (quantity <> 0),
    unit TEXT NOT NULL DEFAULT 'ea',
    unit_price NUMERIC(12,4),
    amount NUMERIC(12,2) NOT NULL,          -- line total after the line's discount
    discount NUMERIC(12,2) NOT NULL DEFAULT 0,
    item_category TEXT NOT NULL REFERENCES finance.item_categories,
    planned TEXT NOT NULL DEFAULT 'Unknown' CHECK (planned IN ('Planned', 'Unplanned', 'Unknown')),
    PRIMARY KEY (receipt_id, line)
);

-- How a receipt was paid, and the bank transaction each payment became.
-- A receipt can be split across cards; a payment is matched to at most one
-- transaction, and a matched receipt adds detail but never a second expense.
CREATE TABLE IF NOT EXISTS finance.receipt_payments (
    payment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES finance.receipts ON DELETE CASCADE,
    tender_text TEXT NOT NULL DEFAULT '',   -- 'VISA 1234 $76.57'
    account_id TEXT REFERENCES finance.accounts,
    amount NUMERIC(12,2) NOT NULL CHECK (amount <> 0),
    transaction_id TEXT REFERENCES finance.transactions ON DELETE SET NULL,
    match_method TEXT CHECK (match_method IN ('auto', 'manual')),
    match_score NUMERIC,                    -- auto matches: 1 = same amount and date
    matched_at TIMESTAMPTZ,
    UNIQUE (receipt_id, transaction_id),
    CHECK ((transaction_id IS NULL) = (match_method IS NULL))
);
CREATE INDEX IF NOT EXISTS receipt_payments_by_transaction ON finance.receipt_payments (transaction_id);

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
-- Balances at a point in time: statement opening and closing balances, running
-- balances printed in exports, balances read off an app or portal, savings
-- buckets, and the account list in each journal entry.
CREATE TABLE IF NOT EXISTS finance.balance_snapshots (
    snapshot_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    as_of DATE NOT NULL REFERENCES core.calendar (day),
    account_id TEXT REFERENCES finance.accounts,
    goal_id TEXT REFERENCES finance.savings_goals ON DELETE CASCADE,
    balance NUMERIC(14,2),                  -- NULL when the source had a blank balance
    balance_kind TEXT NOT NULL DEFAULT 'reported' CHECK (balance_kind IN
        ('statement_opening', 'statement_closing', 'running', 'reported', 'available', 'journal')),
    statement_id BIGINT REFERENCES finance.statements ON DELETE CASCADE,
    document_id BIGINT REFERENCES finance.source_documents ON DELETE CASCADE,
    institution_text TEXT NOT NULL DEFAULT '',
    account_text TEXT NOT NULL DEFAULT '',
    position SMALLINT,                      -- order within a journal entry
    source TEXT NOT NULL CHECK (source IN ('finance_tracker', 'import', 'journal', 'manual')),
    source_ref TEXT,                        -- e.g. the journal entry id
    CHECK (account_id IS NOT NULL OR goal_id IS NOT NULL OR account_text <> '' OR institution_text <> ''),
    CHECK (balance_kind NOT LIKE 'statement_%' OR statement_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS snapshots_by_account ON finance.balance_snapshots (account_id, as_of);
CREATE INDEX IF NOT EXISTS snapshots_by_goal ON finance.balance_snapshots (goal_id, as_of);
CREATE INDEX IF NOT EXISTS snapshots_by_source ON finance.balance_snapshots (source, source_ref);
CREATE UNIQUE INDEX IF NOT EXISTS one_statement_balance_per_kind ON finance.balance_snapshots (statement_id, balance_kind)
    WHERE statement_id IS NOT NULL;

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
DROP VIEW IF EXISTS finance.receipt_matching, finance.spending, finance.transaction_line_items,
                    finance.recurring_monthly, finance.effective_budgets, finance.budget_vs_actual,
                    finance.monthly_spending, finance.pay_periods, finance.pay_period_cash_flow,
                    finance.receipt_reconciliation, finance.item_spending, finance.latest_balances,
                    finance.balance_timeline, finance.statement_reconciliation, finance.running_balances,
                    finance.document_coverage, finance.goal_progress, finance.retirement_summary CASCADE;

-- How much of each receipt is explained by bank transactions.
CREATE VIEW finance.receipt_matching AS
SELECT r.receipt_id, r.purchased_on, r.merchant, r.total,
       COALESCE(SUM(p.amount) FILTER (WHERE p.transaction_id IS NOT NULL), 0) AS matched_amount,
       r.total - COALESCE(SUM(p.amount) FILTER (WHERE p.transaction_id IS NOT NULL), 0) AS unmatched_amount,
       CASE WHEN COUNT(p.transaction_id) = 0 THEN 'unmatched'
            WHEN abs(r.total - COALESCE(SUM(p.amount) FILTER (WHERE p.transaction_id IS NOT NULL), 0)) <= 0.01 THEN 'matched'
            ELSE 'partial' END AS match_status,
       array_remove(array_agg(p.transaction_id), NULL) AS transaction_ids
FROM finance.receipts r
LEFT JOIN finance.receipt_payments p USING (receipt_id)
GROUP BY r.receipt_id;

-- Every expense once. Bank charges carry the category of the receipt that
-- explains them; the part of a receipt no bank charge explains yet is added
-- from the receipt itself.
CREATE VIEW finance.spending AS
SELECT t.transaction_id AS spend_id, t.posted_on AS day, t.account_id, t.merchant,
       COALESCE(rc.category, t.category) AS category, t.amount, 'bank'::text AS origin, rc.receipt_id
FROM finance.transactions t
LEFT JOIN LATERAL (
    SELECT r.receipt_id, r.category FROM finance.receipt_payments p JOIN finance.receipts r USING (receipt_id)
    WHERE p.transaction_id = t.transaction_id ORDER BY abs(p.amount) DESC LIMIT 1
) rc ON true
WHERE t.kind = 'expense'
UNION ALL
SELECT 'receipt-' || m.receipt_id, m.purchased_on,
       (SELECT p.account_id FROM finance.receipt_payments p WHERE p.receipt_id = m.receipt_id AND p.transaction_id IS NULL LIMIT 1),
       m.merchant, r.category, m.unmatched_amount, 'receipt', m.receipt_id
FROM finance.receipt_matching m
JOIN finance.receipts r USING (receipt_id)
WHERE abs(m.unmatched_amount) > 0.01;

-- Receipt lines behind each bank charge: each line with its share of the tax
-- and tip, scaled by the part of the receipt that charge paid.
CREATE VIEW finance.transaction_line_items AS
SELECT p.transaction_id, t.posted_on, t.account_id, r.receipt_id, r.merchant, i.line, i.item_name,
       i.item_category, COALESCE(ic.spending_category, r.category) AS spending_category,
       i.quantity, i.unit, i.amount AS line_amount,
       ROUND(i.amount * (1 + (r.tax + r.tip) / NULLIF(r.net, 0)), 2) AS line_with_tax,
       ROUND(i.amount * (1 + (r.tax + r.tip) / NULLIF(r.net, 0)) * p.amount / NULLIF(r.total, 0), 2) AS allocated_amount
FROM finance.receipt_payments p
JOIN finance.transactions t USING (transaction_id)
JOIN finance.receipts r ON r.receipt_id = p.receipt_id
JOIN finance.receipt_items i ON i.receipt_id = r.receipt_id
LEFT JOIN finance.item_categories ic ON ic.item_category = i.item_category;

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

-- A receipt's lines should equal its net; its matched payments should equal the
-- bank amounts they point to.
CREATE VIEW finance.receipt_reconciliation AS
SELECT r.receipt_id, r.purchased_on, r.merchant, r.net, r.tax, r.tip, r.total,
       li.line_total, li.lines,
       r.net - li.line_total AS lines_vs_net,
       m.match_status, m.matched_amount,
       (SELECT SUM(t.amount - p.amount) FROM finance.receipt_payments p
        JOIN finance.transactions t USING (transaction_id) WHERE p.receipt_id = r.receipt_id) AS bank_vs_payments
FROM finance.receipts r
JOIN finance.receipt_matching m USING (receipt_id)
LEFT JOIN LATERAL (
    SELECT COALESCE(SUM(amount), 0) AS line_total, COUNT(*) AS lines FROM finance.receipt_items i WHERE i.receipt_id = r.receipt_id
) li ON true;

CREATE VIEW finance.item_spending AS
SELECT c.month_start, i.item_category, ic.spending_category, SUM(i.amount) AS amount, SUM(i.discount) AS discounts,
       COUNT(*) AS lines, COUNT(*) FILTER (WHERE i.planned = 'Unplanned') AS unplanned_lines
FROM finance.receipt_items i
JOIN finance.receipts r USING (receipt_id)
JOIN core.calendar c ON c.day = r.purchased_on
LEFT JOIN finance.item_categories ic ON ic.item_category = i.item_category
GROUP BY c.month_start, i.item_category, ic.spending_category;

-- Every known balance of every account over time, newest last.
CREATE VIEW finance.balance_timeline AS
SELECT s.account_id, a.name, a.account_type, at.is_liability, s.as_of, s.balance, s.balance_kind, s.source,
       s.statement_id, s.document_id, d.original_filename
FROM finance.balance_snapshots s
JOIN finance.accounts a USING (account_id)
JOIN finance.account_types at USING (account_type)
LEFT JOIN finance.source_documents d ON d.document_id = s.document_id
WHERE s.balance IS NOT NULL;

CREATE VIEW finance.latest_balances AS
SELECT DISTINCT ON (account_id)
       account_id, name, account_type, is_liability, as_of, balance, balance_kind, source
FROM finance.balance_timeline
ORDER BY account_id, as_of DESC,
         CASE balance_kind WHEN 'statement_closing' THEN 0 WHEN 'reported' THEN 1 WHEN 'available' THEN 2 ELSE 3 END;

-- Opening balance plus the period's transactions should equal the closing
-- balance. A difference means transactions are missing or duplicated.
CREATE VIEW finance.statement_reconciliation AS
SELECT s.statement_id, s.account_id, s.period_start, s.period_end, d.original_filename,
       s.opening_balance, s.closing_balance,
       COUNT(t.transaction_id) AS transactions,
       COALESCE(SUM(t.amount) FILTER (WHERE t.amount > 0), 0) AS money_out,
       COALESCE(-SUM(t.amount) FILTER (WHERE t.amount < 0), 0) AS money_in,
       s.opening_balance + CASE WHEN at.is_liability THEN 1 ELSE -1 END * COALESCE(SUM(t.amount), 0) AS computed_closing,
       s.closing_balance - (s.opening_balance + CASE WHEN at.is_liability THEN 1 ELSE -1 END * COALESCE(SUM(t.amount), 0)) AS difference
FROM finance.statements s
JOIN finance.source_documents d USING (document_id)
JOIN finance.accounts a ON a.account_id = s.account_id
JOIN finance.account_types at USING (account_type)
LEFT JOIN finance.transactions t ON t.account_id = s.account_id AND t.posted_on BETWEEN s.period_start AND s.period_end
                                AND NOT t.is_pending
GROUP BY s.statement_id, d.original_filename, at.is_liability;

-- Balance after each transaction, carried forward from the statement's opening
-- balance, beside the balance the file printed (when it printed one).
CREATE VIEW finance.running_balances AS
SELECT s.statement_id, t.account_id, t.posted_on, t.transaction_id, t.merchant, t.amount,
       s.opening_balance + CASE WHEN at.is_liability THEN 1 ELSE -1 END
           * SUM(t.amount) OVER (PARTITION BY s.statement_id ORDER BY t.posted_on, t.transaction_id) AS computed_balance,
       (SELECT ts.reported_balance FROM finance.transaction_sources ts
        WHERE ts.transaction_id = t.transaction_id AND ts.reported_balance IS NOT NULL LIMIT 1) AS reported_balance
FROM finance.statements s
JOIN finance.accounts a ON a.account_id = s.account_id
JOIN finance.account_types at USING (account_type)
JOIN finance.transactions t ON t.account_id = s.account_id AND t.posted_on BETWEEN s.period_start AND s.period_end
                           AND NOT t.is_pending;

-- For each account and month: which days a statement or export covers, and how
-- many transactions are loaded. Uncovered days are gaps to fill from a folder.
CREATE VIEW finance.document_coverage AS
WITH bounds AS (
    SELECT a.account_id, date_trunc('month', LEAST(MIN(d.period_start), MIN(t.posted_on)))::date AS first_month
    FROM finance.accounts a
    LEFT JOIN finance.source_documents d ON d.account_id = a.account_id
    LEFT JOIN finance.transactions t ON t.account_id = a.account_id
    WHERE a.is_active
    GROUP BY a.account_id
), days AS (
    SELECT b.account_id, c.day, c.month_start FROM bounds b
    JOIN core.calendar c ON c.day BETWEEN b.first_month AND current_date
)
SELECT dy.account_id, dy.month_start,
       COUNT(*) AS days,
       COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM finance.statements s WHERE s.account_id = dy.account_id
                                      AND dy.day BETWEEN s.period_start AND s.period_end)) AS days_with_statement,
       COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM finance.source_documents d WHERE d.account_id = dy.account_id
                                      AND d.doc_type IN ('statement', 'transaction_export')
                                      AND dy.day BETWEEN d.period_start AND d.period_end)) AS days_with_any_document,
       (SELECT COUNT(*) FROM finance.transactions t WHERE t.account_id = dy.account_id
         AND t.posted_on >= dy.month_start AND t.posted_on < dy.month_start + INTERVAL '1 month') AS transactions
FROM days dy
GROUP BY dy.account_id, dy.month_start;

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
