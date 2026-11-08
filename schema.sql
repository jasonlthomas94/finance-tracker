-- Database schema. Create with: psql "$DATABASE_URL" -f schema.sql

CREATE TABLE items (
    id               serial PRIMARY KEY,
    plaid_item_id    text NOT NULL UNIQUE,
    access_token     text NOT NULL,          -- Plaid access token; never print or commit
    institution_name text NOT NULL,
    created_at       timestamp DEFAULT now(),
    sync_cursor      text                    -- /transactions/sync cursor, saved after all pages
);

CREATE TABLE accounts (
    id               serial PRIMARY KEY,
    item_id          integer REFERENCES items(id),
    plaid_account_id text NOT NULL UNIQUE,
    name             text NOT NULL,
    official_name    text,
    account_type     text,                   -- depository, credit, ...
    account_subtype  text,
    mask             text,                   -- last digits, for display
    created_at       timestamp DEFAULT now(),
    display_name     text                    -- optional friendlier label
);

CREATE TABLE transactions (
    id                   serial PRIMARY KEY,
    plaid_transaction_id text NOT NULL UNIQUE,
    account_id           integer REFERENCES accounts(id),
    date                 date NOT NULL,
    name                 text NOT NULL,
    amount               numeric(10,2) NOT NULL,   -- Plaid convention: positive = money out
    category             text,                     -- Plaid personal_finance_category.primary
    pending              boolean DEFAULT false,
    created_at           timestamp DEFAULT now(),
    category_detailed    text                      -- Plaid personal_finance_category.detailed
);

-- Balance per account at each sync (from the accounts list in the
-- /transactions/sync response; Plaid's cached balance, not real-time).
CREATE TABLE account_balances (
    id               serial PRIMARY KEY,
    account_id       integer REFERENCES accounts(id),
    recorded_at      timestamp DEFAULT now(),
    current          numeric(12,2),            -- for credit cards: amount owed
    available        numeric(12,2),
    credit_limit     numeric(12,2),
    plaid_updated_at timestamptz               -- when Plaid last refreshed it, if reported
);
