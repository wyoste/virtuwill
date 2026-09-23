# VirtuWill architecture and data-model review

Reviewed 2026-09-23 against main commit `4dab442838226f75436cdadb9617081758883514` and the proposed tracker integration. This is a source-code assessment and local test review, not an audit of the deployed host or its environment variables. No personal record contents are reproduced here.

## Recommendation

Keep Flask and evolve toward a **modular monolith**: one deployable application, explicit domain boundaries, one relational database, and separate storage for media. Use PostgreSQL for the long-term hosted application, SQLAlchemy for persistence, and Alembic for schema migrations. SQLite remains a reasonable single-host transitional store; switching engines alone does not fix a model made of whole-app JSON blobs.

The current app is a useful personal prototype with recognizable feature modules. Its main weaknesses are inconsistent persistence, weak boundaries between public and private content, and large modules that mix rendering, calculations, network calls, and state. A full frontend rewrite or microservices would add work without resolving those problems first.

The new Finance/Health integration deliberately preserves the supplied standalone apps in isolated frames. Its private SQLite documents, authenticated API, save status, and revision checks improve integration and durability. **It is a migration bridge, not the final normalized database or shared component architecture.** The wrapper does not repair every validation or calculation issue inside the original tracker apps. It also hosts a single owner's pair of trackers; it is not a multi-user authorization model.

## Findings and priorities

| Priority | Evidence in the repo | Consequence | Recommended change |
|---|---|---|---|
| P0 | `git ls-files data` includes journal entries and contact messages; repository metadata says public | App login cannot protect files already in repository history | Review these files privately, back them up, remove sensitive runtime files from tracking, and assess history cleanup. Adding `.gitignore` does not remove historical commits. Do not rewrite shared history without a coordinated decision. |
| P0 | `config.py` provides a known session secret and known passwords; debug defaults on | If deployed without overrides, sessions and authentication are unsafe | Fail production startup without strong configuration; separate development fixtures; use a password hash or managed identity, rate limiting, and secure session settings. The new tracker routes fail closed on the old defaults; older routes still need hardening. |
| P1 | `music_catalog_get()` returns the entire catalog; `music_library_scan()` lists all files; publication filtering lives in `music.js` | Draft music is hidden visually but metadata and static audio remain accessible | Filter public API responses on the server. Store private masters/demos outside public static storage and authorize asset delivery. A draft flag alone cannot revoke an existing public audio URL. |
| P1 | Portfolio uploads are written to `static/portfolio`; `templates/pages/project.html` has an unsandboxed iframe | Uploaded HTML runs with the main site's origin when opened directly or embedded | Serve uploaded HTML on a separate origin or through a protected, sandboxed route. Treat uploaded executable content differently from images and audio. |
| P1 | `_save`, `save_music_catalog`, and other helpers rewrite full JSON files without locking or atomic replacement | Concurrent writes can overwrite each other; interrupted writes can corrupt a file | Transactions, record-level writes, revision checks, and unique constraints in a database. Do not silently load mock data on corruption. |
| P1 | `journal.js` saves update local state and show “Entry saved” without checking `response.ok`; `music.js` swallows save errors | Users can believe changes were saved when the server rejected them | One API client that rejects non-2xx responses; visible pending/error states; retain dirty edits and offer retry. |
| P1 | Admin and journal use independent session flags and passwords; `VW.Auth.login()` ignores the journal unlock response | UI can consider the journal unlocked when the backend does not | One authenticated user identity and explicit permissions. Derive journal access from that identity. |
| P1 | Existing mutations use repeated inline session checks; no shared CSRF layer or consistent payload/file limits | Controls and validation vary by feature | Shared authentication/CSRF middleware, request schemas, size limits, content checks, and safe generated asset names. |
| P1 | `/api/accounts-template` GET is public | Account labels intended for a private journal can be exposed | Require owner access, then migrate templates to private financial-account records. |
| P2 | Baseline `app.py` is about 800 lines; music, garden, and journal JS are roughly 1,300–1,600 lines each | Changes cross unrelated responsibilities and are harder to test | Split by domain and responsibility; reduce coupling before selecting a new UI framework. |
| P2 | Travel and portfolio customization use browser-local storage while other domains use server JSON | Devices disagree about the user's data | Put durable user state on the server. Reserve local storage for non-sensitive preferences and recoverable caches. |
| P2 | `templates/index.html` has two `admin-banner` IDs and large inline style blocks; templates have inline handlers | DOM selection is ambiguous and refactoring event flows is brittle | Unique IDs, scoped component styles, event listeners/event delegation, consistent lifecycle functions. |
| P2 | `chat.js` and admin chat history point to `http://localhost:8765` | On a visitor's phone, localhost refers to that phone, not the deployed server | Configure an authenticated backend proxy or an explicit optional local-only feature. |
| P2 | Tracked `__pycache__`, large audio files, and README claims that no inline scripts/styles exist | Repository and docs diverge from real architecture | Keep generated files and private records out of Git; move media to asset storage; update docs and define reproducible dependencies/CI. |

Do not read every `innerHTML` use as an automatically proven exploit. However, the mixture of HTML strings, inline handlers, rich-text journal content, and escaped strings requires a dedicated context-aware rendering review. HTML text escaping does not automatically make a value safe inside JavaScript event attributes. Prefer `textContent` for plain text and a vetted sanitizer with a narrow allowlist for rich text.

## Proposed code structure

| Location | Responsibility |
|---|---|
| `virtuwill/__init__.py` | `create_app(config)`; register extensions, blueprints, logging, error handling |
| `virtuwill/auth/` | User sessions, permissions, CSRF, login throttling |
| `virtuwill/finance/` | Routes, request schemas, budget/transfer/import services, models |
| `virtuwill/health/` | Routes, schemas, measurement/nutrition/workout services, models |
| `virtuwill/journal/` | Daily entries, tags, revisions, OCR workflow |
| `virtuwill/music/` | Compositions, recordings, releases, public catalog, private asset rules |
| `virtuwill/media/` | Upload validation, object-storage access, metadata, authorized delivery |
| `virtuwill/shared/` | Database setup, clocks, identifiers, consistent API errors; avoid a generic “utils” dumping ground |
| `migrations/` | Ordered, reviewed Alembic migrations |
| `frontend/core/` | API client, navigation, session state, form/save states |
| `frontend/features/<domain>/` | Views, domain state, pure calculations, API adapter |
| `tests/` | Domain invariants, persistence/authorization integration tests, critical browser flows |

Route handlers should parse and authorize requests, call a service, and return a response. Services own business rules and transaction boundaries; models and query functions own persistence. Do not introduce generic repositories for every table merely to wrap ORM calls.

Start by using native ES modules and separating pure calculations from DOM rendering. Introduce TypeScript and a build tool when typed domain contracts justify the added build step. Decide on React/Vue only if reusable interactive UI has become difficult after the boundary cleanup. The admin dashboard should call the same domain services as the rest of the application instead of reconstructing data by reading other modules' DOM nodes.

## Database design

Use UUID primary keys, foreign keys, UTC `timestamptz` audit timestamps, and explicit local `date` fields for daily behavior. Keep `created_by`, `updated_by`, `created_at`, `updated_at`, and an integer `version` on mutable aggregate roots. Every private query must scope by an authenticated owner or authorized household. Add indexes for those owner/date access paths.

Use relational columns for fields used in filters, joins, constraints, or totals. Use JSONB only for limited flexible metadata and raw import payloads—not as the authoritative representation of every feature. Media bytes belong in object storage; the database holds metadata and references.

### Identity and common records

| Table | Main fields and rules |
|---|---|
| `users` | `id`, unique login/provider subject, display name, timezone, password hash if local auth |
| `households`, `household_members` | Household identity and member role; unique membership pair. Household finance sharing does not automatically grant access to individual health or journal records. |
| `media_assets` | Owner, unique storage key, media type, size, checksum, original filename, visibility, upload status |
| `import_batches` | Owner, source, checksum, status, row counts, timestamps; immutable source provenance and an idempotency key |
| `import_records` | Batch, source record key, raw JSONB, outcome/error; bounded retention and private access |
| `audit_events` | Actor, domain/object ID, operation, timestamp; redact private text, tokens, and financial payloads from logs |

For household finance tables, carry `household_id` consistently and use composite foreign keys or equivalent checks to prevent cross-household references. For individual health/journal tables, use `user_id` and enforce ownership on all reads and writes. Database row-level security can be added as defense in depth when connection identity is handled correctly; it does not replace application authorization.

### Finances

| Table | Purpose and important fields |
|---|---|
| `financial_accounts` | Household, institution, display name, account type, currency, archived flag; no need to retain complete account numbers |
| `account_balances` | Account, as-of date/time, exact balance, source; distinguish observed valuations from computed cash-flow estimates |
| `financial_transactions` | Account, posted date, signed `NUMERIC(18,2)` amount, currency, merchant/payee, transaction kind, import record, external ID, transfer group, status |
| `transaction_splits`, `spending_categories` | Category allocation within a transaction; splits must sum exactly to its amount. Hierarchical categories support granular groceries. |
| `budget_periods`, `budget_lines` | Household, start/end dates, scope, category, planned amount; distinguish actual budgets from contribution/allocation plans |
| `recurring_rules`, `scheduled_occurrences` | Paychecks, bills, transfers: recurrence, anchor, account(s), amount, timezone, effective dates; scheduled occurrences are forecasts until matched to actual transactions |
| `financial_goals`, `goal_allocations` | Target amount/date and actual earmarked funds or matched contributions. Goal balances must not independently create money. |
| `receipts`, `receipt_items`, `receipt_payments` | Merchant/date, net/tax/total; line product/quantity/unit/net amount/category; links to one or more payment transactions |
| `products`, `product_food_links` | Purchased product identity and an optional reviewed link to a health food/serving definition |
| `retirement_plans`, `retirement_contributions` | Account, contribution date, tax year, employee/employer/source amount, contribution type, optional payroll/transaction link |
| `contribution_policies` | Tax year, policy type, limits and effective dates; no permanent hardcoded annual limits |

**Financial invariants:** Store money with an exact numeric representation and return decimal strings or minor units through JSON contracts. Never aggregate unlike currencies without an explicit conversion rate/date. A transfer links two account movements and is excluded from income/spending totals; fees remain expenses. Credit-card payments are transfers, while the underlying purchases are expenses. Refunds reverse the appropriate expense category. A receipt enriches a payment: adding receipt lines must not count that purchase a second time. Investments need observed balance snapshots because transactions alone do not represent market gains/losses.

Reconcile receipt net + tax with total, discounts with net line values, receipt payments with the linked payment allocations, and splits with transaction totals in one service transaction. Cross-row sums need transaction logic or deferred constraint triggers; ordinary row CHECK constraints are insufficient. External-ID uniqueness should be scoped to owner, source, and account. Fall back to a stable source fingerprint when imports lack reliable IDs; do not deduplicate solely by date and amount.

### Health

| Table | Purpose and important fields |
|---|---|
| `health_profiles` | User, height/unit, relevant calculation inputs, effective date; retain history when profile inputs change |
| `health_goals` | User, metric, target, unit, effective dates, phase; distinct weight/BMI/nutrition/activity goals |
| `body_measurements` | User, observed time, local date, metric, value/unit, morning/reference conditions, source, note |
| `foods`, `food_servings` | Food/product identity, serving quantity/unit/grams, nutrient values, source URL, verification date, version |
| `meal_entries`, `meal_items` | User/date/meal slot, planned/eaten status, food/serving, quantity, **nutrient snapshot at logging time** |
| `recipes`, `recipe_ingredients` | Owner, yield/servings, ingredient serving/quantity, recipe version |
| `workout_sessions` | User, date/time, activity, duration, note, source; dog walks explicitly identified |
| `workout_exercises`, `exercise_sets` | Optional detail: exercise, set order, reps, load/unit, duration |
| `daily_health_logs` | User/local date, nutrition-complete flag; a missing log is not zero intake |
| `habit_definitions`, `habit_logs` | User, local date, typed value, unit, privacy; distinguish unknown from false/zero |

Calculate weekly workout progress from distinct local dates whose qualifying sessions total at least 45 minutes; separately track dog walks. Compute trends from compatible measurement conditions. Preserve the original afternoon reference rather than relabeling it as a morning reading. A food-label correction should affect future entries, not silently rewrite past calories. Purchased groceries never become consumed meals automatically. Store alcohol quantity, serving size, and strength if that feature remains, and prevent duplicate calorie counting when also logged as a meal item.

### Journal

| Table | Purpose and important fields |
|---|---|
| `journal_entries` | User, local `entry_date`, quote/author, body plus format, source, privacy, version; `UNIQUE(user_id, entry_date)` for one daily entry |
| `journal_entry_revisions` | Entry, revision number, editor/time, prior content or patch; unique entry/revision |
| `tags`, `journal_entry_tags` | Owner-scoped tags and explicit association |
| `journal_entry_assets` | Entry ↔ private photo/document attachment |
| `journal_meal_notes` | Optional free-text breakfast/lunch/dinner descriptions that are not structured nutrition records |

Health measurements, workout sessions, and financial account balances remain in their own tables. The journal's daily screen composes them by authenticated owner and local date, with optional explicit links when needed. Do not copy account-balance snapshots and meal macros into the journal's primary row. Preserve legacy unstructured notes during migration and mark them as notes; do not invent numeric nutrition or transactions from prose.

### Music

| Table | Purpose and important fields |
|---|---|
| `songs` | Owner, title, composition metadata, key, BPM, story, writing location |
| `song_sections` | Song/version, ordered position, type, lyrics/chords/tab content; preserve author formatting |
| `recordings` | Song, version label, audio asset, duration, recording date, draft/published status |
| `releases`, `release_tracks` | Album/EP/single metadata, artwork; recording link, disc/track order with uniqueness |
| `music_assets` | Additional artwork, photos, lead sheets, and their relationship to the song/release |

Separate the composition from recordings: one song may have several demos and masters, and one recording may appear on multiple releases. Serve public catalog metadata from explicitly published rows. Use signed or authenticated access for draft assets; published content can use a public delivery path. Avoid independently scanning the filesystem as a second authoritative catalog.

## Relationships that make the app useful

| User experience | Authoritative data path |
|---|---|
| Daily journal shows weight and workouts | Journal date → user's measurements and workout sessions |
| Grocery spending informs meal planning | Receipt items → reviewed product-to-food link → food serving/recipe suggestions |
| Budget reports match actual purchases | Account transactions → expense splits; receipt lines explain purchases without adding spend |
| Retirement dashboard separates saving from growth | Retirement contributions + account balance snapshots, displayed as different measures |
| Music portfolio shows finished releases while admin keeps demos | Published releases/recordings API versus authenticated full catalog and protected assets |

## Migration sequence and acceptance gates

1. **Protect and stabilize.** Review tracked runtime data, fix public/private access, configure secrets, standardize checked saves, and add tests for auth and data loss. Keep backups before altering persistence. Do not perform repository history rewriting as an incidental refactor.
2. **Create the modular backend.** Add the app factory, shared auth/API errors, SQLAlchemy session setup, and Alembic baseline. Existing URLs can remain compatible while services replace route-local logic.
3. **Migrate journal and music first.** They have smaller, clearer schemas. Import stable legacy IDs, reconcile counts and media references, detect duplicate journal dates for manual resolution, and make dry-run import reports repeatable. Never silently select one duplicate daily entry.
4. **Normalize finance.** Import accounts, transactions, receipts, budgets, goals, payroll, and retirement data with provenance. Gate cutover on matching transaction totals by account/month, category spending, transfer exclusions, receipt reconciliation, and contribution totals.
5. **Normalize health.** Import profile/goals, food references, recipes, measurements, meals, workouts, and habit logs. Compare weekly qualifying days and morning-weight trends with the existing tracker. Preserve units, logged nutrient snapshots, and planned-versus-eaten distinctions.
6. **Replace embedded views incrementally.** Build native admin views against domain APIs, then retire HTML hosting and whole-state writes once feature parity and imports are verified. Keep legacy exports read-only for a defined recovery window.

Use a short write freeze or a versioned final delta import for each cutover. Avoid open-ended dual writing to JSON and SQL. Back up original inputs and verify a database restore before disabling the old writer. Rollback needs a snapshot or reversible migration and an explicit plan for writes made after cutover.

## Validation and limits

The integration has automated API tests for authorization, default-credential rejection, CSRF, schema rejection, optimistic concurrency, restart persistence, private responses, sandbox headers, and logout. An optional browser smoke script accepts local tracker files without committing them. It exercises real tracker tabs and forms, saves, backup restore, portable HTML export, stale-tab conflict handling, mobile viewport width, and logout.

This review does not establish the production deployment's storage durability, backup schedule, current credentials, or whether tracked data is synthetic. Those require a host/configuration review and owner confirmation about the records. The PostgreSQL model is a proposal; this integration PR does not create those tables or migrate existing journal/music data.

## Primary references

- [Flask blueprints](https://flask.palletsprojects.com/en/stable/blueprints/) and [application factories](https://flask.palletsprojects.com/en/stable/patterns/appfactories/): supported mechanisms for the proposed modular backend.
- [PostgreSQL numeric types](https://www.postgresql.org/docs/current/datatype-numeric.html): exact monetary representation.
- [PostgreSQL constraints](https://www.postgresql.org/docs/current/ddl-constraints.html): keys, references, and row/relational integrity.
- [PostgreSQL JSON types](https://www.postgresql.org/docs/current/datatype-json.html): bounded use of JSONB alongside relational fields.
- [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html): versioned SQLAlchemy database migrations.
