# finance-tracker

Personal finance pipeline for one household: Plaid -> Postgres -> checks -> HTML report, emailed monthly. Everything runs locally. Household-specific facts live in `CLAUDE.local.md` (not committed) and settings in `household.toml` (not committed).

## Data safety

- This handles real bank data. Never print, log, commit or paste access tokens, secrets, raw transaction dumps, account numbers, pay amounts or names.
- Never commit `.env`, `household.toml`, `CLAUDE.local.md`, `output/` or `venv/` (all gitignored). Before any commit, scan staged files for amounts, names, account fragments and email addresses.
- Code and committed docs must stay generic: personal payees, amounts and dates belong in `household.toml`. Tests build their cases from the active config instead of literals.

## Stack and how to run

- Python 3.14 in `venv/` (direnv), Postgres 16 (`DATABASE_URL` in `.env`), local Ollama `llama3.1:8b` for one checked opening sentence.
- `household.py` loads `household.toml` (or `$HOUSEHOLD_CONFIG`, or falls back to `household.example.toml`, which has made-up values).
- `sync_transactions.py`: cursor-based `/transactions/sync` per bank item. Follows `has_more`, applies added/modified/removed, retries on mutation during pagination, saves the cursor only after all pages are written, stores Plaid's primary and detailed category. A failing item is rolled back, others still sync, and it exits 1 with a fix message. Never use the paid refresh endpoint.
- `link_account.py`: one-time per new bank item (token pasted temporarily, then removed).
- `queries.py`: all SQL and the rules built from `household.toml`.
- `generate_report.py` writes `output/report.html` (and `output/summary.txt` for the email body). It runs `checks.py` first and refuses to write if any check fails.
- `attention.py`: things the settings don't explain after `reviewed_through` (shown as "Needs attention" in the report).
- `scheduler.py`: weekly (Mondays 07:00) and monthly (4th, 07:00, emails the report via Mail.app) runs, started by a launchd agent; catches up after sleep or shutdown.
- `generate_summary.py`, `generate_charts.py`, `generate_narrative.py`: terminal views of the same data.
- `demo/`: synthetic data for a demo database, used for the README screenshot.

## Schema

- `items(id, plaid_item_id, access_token, institution_name, sync_cursor)`.
- `accounts(id, item_id, plaid_account_id, name, official_name, account_type, account_subtype, mask, display_name)`.
- `transactions(id, plaid_transaction_id UNIQUE, account_id, date, name, amount, category, pending, created_at, category_detailed)`.
- Plaid sign convention: positive amount = money out, negative = money in.

## Rules (configured in household.toml)

- Regular spend excludes transfers, savings movements, payments to tracked cards, annual bills, and anything at or above `one_off_threshold` (fixed bills excepted).
- Fixed bills are always regular spend with a stable label. Expense classes split regular spend into fixed-necessary, fixed-discretionary and variable.
- Cash flow is computed on checking accounts only. Savings (deposits minus withdrawals) is shown separately.
- Monthly verdict: one rule, "did we spend less than we earned?", with annual bills spread out as a monthly set-aside. The savings change is shown as a fact, not judged.
- Household categories: merchant rules from config first, then Plaid detailed categories, then `Other`.

## Narrative

Code writes every sentence that contains a number, name, date or direction. The local model writes only a one-sentence opener from the month name and a direction word; `check_opener` rejects numbers, superlatives, advice or the wrong direction, with a fixed fallback. Do not give the model numbers or names: in testing it mislabeled most full narratives.

## Working style

Direct and concise. Say plainly when something is wrong. Verify numbers reconcile after any change (`python checks.py`).
