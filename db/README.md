# VirtuWill data model

The Lakebase (PostgreSQL) data model the whole app runs on. `db/schema/*.sql` is a
versioned sequence: each file runs once, in name order, recorded with its checksum in
`virtuwill.schema_versions`. Never edit a file that has run; put a change (a new
column, a rebuilt view) in a new, higher-numbered file.

A browsable version with the semantic model, diagrams and every table is in
[`docs/lakebase-model.html`](../docs/lakebase-model.html) (open it in a browser).

The application reads and writes these tables through the `virtuwill/` package, one
module per schema. `virtuwill/migrate.py` moved the earlier Lakebase layout in once.

## Schemas

| Schema | Holds | Visibility |
|---|---|---|
| `core` | `calendar` (conformed date dimension, 1900–2100), `media_assets` (every file), `daily_summary` view | shared |
| `journal` | `entries` (one per date), `entry_tags`, `habits`, `habit_logs`, `meals`, `workout_types`, `workouts` + `derived_habits`, `day_habits` views | private |
| `health` | `body_measurements` (many weigh-ins per date), `alcohol`, `daily_logs`, `foods`, `recipes`, `recipe_ingredients`, `profile`, `profile_history`, `goals` + 4 views | private |
| `finance` | `account_types`, `accounts`, `account_aliases`, `import_profiles`, `import_batches`, `source_documents`, `statements`, `categories`, `movement_types`, `transactions`, `transaction_sources`, `receipts`, `item_categories`, `item_catalog`, `receipt_items`, `receipt_payments`, `shopping_list`, `budgets`, `budget_categories`, `recurring_expenses`, `pay_profile`, `paycheck_deposits`, `other_incomes`, `allocations`, `savings_goals`, `balance_snapshots`, `retirement_plan` + 18 views | private |
| `garden` | `settings`, `species`, `health_levels`, `beds`, `seasons`, `plantings`, `plant_observations`, `photos`, `photo_subjects` + 2 views | public read |
| `music` | `albums`, `songs` (each with a URL `slug`), `song_sections`, `recordings`, `gallery_photos` + `public_catalog` view | published songs, and published recordings on published albums |
| `content` | `blog_posts`, `portfolio_projects`, `project_metrics`, `project_timeline`, `site_text`, `messages` + `public_posts` view | posts/projects public; messages private |
| `career` | `profile` (one row), `roles`, `education`, `skill_groups`, `highlights` (pillars, impact figures, strengths, certifications); `content.portfolio_projects.role_id` links each project to its role | public read |
| `travel` | `places`, `place_photos`, `visited_regions` | public read |
| `virtuwill` | `schema_versions`, `migrations`, `trackers` (Finance/Health tracker documents), `sync_reports` | private |

## Conventions

- **Dates** are local calendar dates in `APP_TIMEZONE` and reference `core.calendar(day)`.
  The calendar is the conformed dimension every domain joins on.
- **Money** is `NUMERIC(12,2)` US dollars. Transaction amounts keep the bank's sign:
  positive is money leaving the account, negative is money coming in.
- **Sources**: rows copied from an embedded tracker carry `source` and a stable
  `source_ref` (unique per source) and keep the original record in `details`. A save
  upserts by `source_ref` and removes only that source's rows that disappeared, so
  unchanged records keep their ids. Each writer touches only its own rows.
- **Units**: weights keep what was entered (`value`, `unit`) and every calculation uses
  the generated `value_lb`.
- **Unknown is not zero**: missing calories, minutes, prices or balances stay `NULL`.
- **Single owner**: there is no `user_id`; every row belongs to the site owner.
- **Settings**: `core.settings` holds owner switches such as `site.chat_enabled`.
- **Derived habits**: `journal.habits.derived_from` ties run, lift and drink to the day's
  records (`journal.derived_habits`); `journal.day_habits` shows each day's habits, where a
  `habit_logs` row set by the owner wins over the derivation.
- **One editor per record**: workspace screens write through `/api/v1`. The Health
  tracker is retired, so its former rows are edited like any other; the Finance
  tracker still owns the finance rows it projects until Money imports replace it.

## Statements, exports and receipts

Folders of statement PDFs, CSV exports and receipts feed the finance schema:

1. **Register the file.** `source_documents` records each file once by SHA-256, with
   its account, the period it covers and the `import_profiles` entry that says how to
   read it (CSV column map, sign convention, file-name pattern, PDF parser). The file
   is a private `core.media_assets` row. `import_batches` records each folder run.
2. **Balances at a point in time.** A statement's printed balances go to `statements`,
   and its opening and closing balances to `balance_snapshots` (`statement_opening`,
   `statement_closing`). Running balances printed in exports are kept per row in
   `transaction_sources.reported_balance`; balances read off an app or portal are
   `reported` snapshots.
3. **Bank-side transactions.** Each real transaction is one `transactions` row, found
   again by its bank id or a fingerprint (account, date, amount, description, same-day
   order). Every file it appears in adds a `transaction_sources` row with the page or
   row, so the CSV and the PDF never double it.
4. **Receipt lines.** `receipts` (any store) and `receipt_items` hold the detail;
   `receipt_payments` records each tender and the bank transaction it became. Split
   payments, partial matches and returns fit; a matched receipt adds detail, never a
   second expense.

Checks the views provide:

| View | Question it answers |
|---|---|
| `statement_reconciliation` | Does opening balance + the period's transactions equal the closing balance? A difference means missing or duplicate transactions. |
| `running_balances` | After each transaction, does the balance carried from the statement match the balance the file printed? |
| `document_coverage` | For each account and month, which days a statement or export covers, and how many transactions are loaded. |
| `receipt_matching` | Is each receipt matched, partly matched or unmatched to bank transactions? |
| `transaction_line_items` | Which receipt lines make up a bank charge, with tax and tip shared across lines and scaled to the part of the receipt that charge paid. |
| `balance_timeline`, `latest_balances` | Every known balance of every account over time, and the latest. |

Imports (`97_finance_imports.sql`) add `staged_imports` (each upload's extracted
records, its preview and, once committed, what it did; one committed import per file
hash), `paychecks` with `paycheck_lines` and `paycheck_splits` (actual pay, beside the
planned `pay_profile`), and the views `daily_spending`, `current_balances` (latest
balance, activity since, and an estimate flagged `estimate_complete` only when both
charges and payments/deposits have loaded since) and `monthly_pay`.

For credit cards and loans the balance is the amount owed, so charges raise it
(`account_types.is_liability`); for deposit accounts charges lower it.

## How today's data maps

| Today | Model |
|---|---|
| `data/journal_entries.json` | `journal.entries`, `entry_tags`, `habit_logs`, `meals` (source `journal`); each entry's account list → `finance.balance_snapshots` (source `journal`) |
| Health tracker state (`yoste-health-v1`) | workouts → `journal.workouts`; meals → `journal.meals`; weights → `health.body_measurements`; beers → `health.alcohol`; complete → `health.daily_logs`; foods → `health.foods`; settings → `health.profile` + derived `health.goals` |
| Health tracker seed | recipes → `health.recipes`, `recipe_ingredients` |
| Finance tracker state (`yoste-finance-spa-v1`) | transactions + movements → `finance.transactions`, with the statement each row names (`file.pdf, p. 5`) → `source_documents` + `transaction_sources`; receipts/items → `receipts`, `receipt_items`, and each receipt's payment → `receipt_payments` matched to its bank charge; catalog → `item_catalog`; budgets + groceryBudget → `budgets`, `budget_categories`; expenses → `recurring_expenses`; payroll → `pay_profile`; deposits → `paycheck_deposits`; incomes → `other_incomes`; allocations → `allocations`; goals → `savings_goals` + `balance_snapshots`; retirement → accounts + `balance_snapshots`; retirementPlan → `retirement_plan`; shopping → `shopping_list` |
| `data/garden.json` + species in `garden.js` | `garden.settings`, `species`, `beds`, `seasons` (one "Current" season per bed), `plantings` |
| `data/garden_photos.json` | `garden.photos`, `photo_subjects` |
| `data/music_catalog.json`, `static/audio/` | `music.songs`, `song_sections` (legacy whole-song text → one `full` section), `albums` (audio folders), `recordings` (audio files) |
| `data/blog.json`, `messages.json` | `content.blog_posts`, `content.messages` |
| Built-in projects in `resume.js`, `portfolio_uploads.json`, `vw_portfolio_state` | `content.portfolio_projects`, `project_metrics`, `project_timeline` |
| The CV written into the old Resume page (`db/seed/career.json`, loaded once) | `career.profile`, `roles`, `education`, `skill_groups`, `highlights` |
| Garden gallery note/hero (`localStorage`) | `content.site_text` |
| Travel pins/visited (`localStorage`) | `travel.places`, `place_photos`, `visited_regions` |
| `data/accounts_template.json` | `finance.accounts.show_in_journal` / `journal_position` |
| Uploaded and bundled files | `core.media_assets` |

## Validation

The schema was applied twice to an empty database as a role with only
`CONNECT` and `CREATE`, then loaded with the real data (journal, both trackers,
garden, music catalog, blog, messages, portfolio). All constraints held:

- Finance: every bank charge and movement loaded; every receipt reconciled to the cent
  (lines = net, net + tax = total); receipts matched to bank charges one-to-one, and
  unmatched receipts counted once from the receipt alone. Per-paycheck and
  recurring-bill budgets derived correctly.
- Garden: rectangle, circle and polygon beds with all plantings and species.
- Health: foods, recipes, the profile and the goals derived from it.

Personal figures from the validation are not recorded here because this
repository is public.

## Open questions for the owner

1. **Account names and types.** Statements, receipts and paycheck deposits name
   accounts only by institution and last four digits, and some cards appear only on
   receipts. Each account needs an institution, a name and a type (listed privately
   with the owner, not here).
2. **Retirement account institutions** for the retirement balances.
3. **Journal account list**: some journal entries list an institution without an
   account name.
4. **Music links**: "Dance with me" points to an audio file that isn't in `static/audio`,
   and "State of Mind" points to the root folder while the file is in `Deep Cuts/`.
5. **Discretionary pool membership** is taken from the budget's note (Shopping, Dining,
   Entertainment, Personal, Health, Professional, Home, Fees, Review). Confirm.
6. **Statement files.** The tracker names the statements its rows came from, but not
   their periods or balances. Loading the PDF and CSV folders fills `statements`,
   `balance_snapshots` and `document_coverage`. A sample CSV and statement from each
   institution is needed to write their import profiles.
