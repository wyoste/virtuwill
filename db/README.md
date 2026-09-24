# VirtuWill data model

The target model for the Lakebase (PostgreSQL) database. `db/schema/*.sql` run in
name order; each file is idempotent and safe to rerun on every start. Views are
dropped and recreated so their columns always match the files.

This is the model only. The application still reads and writes through
`storage.py` / `lakebase_model.py`; moving the app onto these schemas is the next step.

## Schemas

| Schema | Holds | Visibility |
|---|---|---|
| `core` | `calendar` (conformed date dimension, 2015–2040), `media_assets` (every file), `daily_summary` view | shared |
| `journal` | `entries` (one per date), `entry_tags`, `habits`, `habit_logs`, `meals`, `workout_types`, `workouts` | private |
| `health` | `body_measurements` (many weigh-ins per date), `alcohol`, `daily_logs`, `foods`, `recipes`, `recipe_ingredients`, `profile`, `profile_history`, `goals` + 4 views | private |
| `finance` | `accounts`, `account_aliases`, `categories`, `movement_types`, `transactions`, `receipts`, `receipt_items`, `grocery_categories`, `grocery_catalog`, `shopping_list`, `budgets`, `budget_categories`, `recurring_expenses`, `pay_profile`, `paycheck_deposits`, `other_incomes`, `allocations`, `savings_goals`, `balance_snapshots`, `retirement_plan` + 12 views | private |
| `garden` | `settings`, `species`, `health_levels`, `beds`, `seasons`, `plantings`, `plant_observations`, `photos`, `photo_subjects` + 2 views | public read |
| `music` | `albums`, `songs`, `song_sections`, `recordings`, `gallery_photos` + `public_catalog` view | published rows public |
| `content` | `blog_posts`, `portfolio_projects`, `project_metrics`, `project_timeline`, `site_text`, `messages` + `public_posts` view | posts/projects public; messages private |
| `travel` | `places`, `place_photos`, `visited_regions` | public read |
| `virtuwill` | `migrations`, `trackers` (Finance/Health tracker documents), `sync_reports` | private |

## Conventions

- **Dates** are local calendar dates in `APP_TIMEZONE` and reference `core.calendar(day)`.
  The calendar is the conformed dimension every domain joins on.
- **Money** is `NUMERIC(12,2)` US dollars. Transaction amounts keep the bank's sign:
  positive is money leaving the account, negative is money coming in.
- **Sources**: rows copied from an embedded tracker carry `source` and `source_ref`
  (the tracker's own id) and keep the original record in `details`. Each writer
  replaces only its own rows.
- **Unknown is not zero**: missing calories, minutes, prices or balances stay `NULL`.
- **Single owner**: there is no `user_id`; every row belongs to the site owner.

## How today's data maps

| Today | Model |
|---|---|
| `data/journal_entries.json` | `journal.entries`, `entry_tags`, `habit_logs`, `meals` (source `journal`); each entry's account list → `finance.balance_snapshots` (source `journal`) |
| Health tracker state (`yoste-health-v1`) | workouts → `journal.workouts`; meals → `journal.meals`; weights → `health.body_measurements`; beers → `health.alcohol`; complete → `health.daily_logs`; foods → `health.foods`; settings → `health.profile` + derived `health.goals` |
| Health tracker seed | recipes → `health.recipes`, `recipe_ingredients` |
| Finance tracker state (`yoste-finance-spa-v1`) | transactions + movements → `finance.transactions`; receipts/items → `receipts`, `receipt_items`; catalog → `grocery_catalog`; budgets + groceryBudget → `budgets`, `budget_categories`; expenses → `recurring_expenses`; payroll → `pay_profile`; deposits → `paycheck_deposits`; incomes → `other_incomes`; allocations → `allocations`; goals → `savings_goals` + `balance_snapshots`; retirement → accounts + `balance_snapshots`; retirementPlan → `retirement_plan`; shopping → `shopping_list` |
| `data/garden.json` + species in `garden.js` | `garden.settings`, `species`, `beds`, `seasons` (one "Current" season per bed), `plantings` |
| `data/garden_photos.json` | `garden.photos`, `photo_subjects` |
| `data/music_catalog.json`, `static/audio/` | `music.songs`, `song_sections` (legacy whole-song text → one `full` section), `albums` (audio folders), `recordings` (audio files) |
| `data/blog.json`, `messages.json` | `content.blog_posts`, `content.messages` |
| Built-in projects in `resume.js`, `portfolio_uploads.json`, `vw_portfolio_state` | `content.portfolio_projects`, `project_metrics`, `project_timeline` |
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
6. **Finance tracker `coverage`** is empty in the current data; its purpose is unknown,
   so it isn't modeled yet.
