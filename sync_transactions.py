import json
import os
import sys
from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.exceptions import ApiException
import psycopg2

load_dotenv()

configuration = plaid.Configuration(
    host=plaid.Environment.Production,
    api_key={
        'clientId': os.getenv('PLAID_CLIENT_ID'),
        'secret': os.getenv('PLAID_SECRET'),
    }
)
client = plaid_api.PlaidApi(plaid.ApiClient(configuration))

MAX_RETRIES = 3
STALE_PENDING_DAYS = 14


def detailed_category_of(txn):
    pfc = txn.get('personal_finance_category')
    return pfc['detailed'] if pfc else None


def category_of(txn):
    pfc = txn.get('personal_finance_category')
    return pfc['primary'] if pfc else None


def fetch_all_pages(access_token, start_cursor):
    """Follow has_more until Plaid says we're caught up. Returns everything
    plus the final cursor. Nothing is written to the DB until all pages succeed."""
    cursor = start_cursor
    added, modified, removed = [], [], []
    while True:
        kwargs = {"access_token": access_token}
        if cursor:
            kwargs["cursor"] = cursor
        resp = client.transactions_sync(TransactionsSyncRequest(**kwargs))
        added += resp['added']
        modified += resp['modified']
        removed += resp['removed']
        cursor = resp['next_cursor']
        if not resp['has_more']:
            return added, modified, removed, cursor


def sync_item(cur, db_item_id, access_token, institution_name, start_cursor):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            added, modified, removed, new_cursor = fetch_all_pages(access_token, start_cursor)
            break
        except ApiException as e:
            if 'TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION' in str(e.body) and attempt < MAX_RETRIES:
                print(f"  Data changed mid-pagination, restarting (attempt {attempt})...")
                continue
            raise
    else:
        return

    cur.execute("SELECT plaid_account_id, id FROM accounts WHERE item_id = %s", (db_item_id,))
    account_id_map = dict(cur.fetchall())

    inserted = updated = skipped = 0
    for txn in added + modified:
        db_account_id = account_id_map.get(txn['account_id'])
        if not db_account_id:
            skipped += 1
            continue
        cur.execute("""
            INSERT INTO transactions (plaid_transaction_id, account_id, date, name, amount, category, category_detailed, pending)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (plaid_transaction_id) DO UPDATE SET
                account_id = EXCLUDED.account_id,
                date = EXCLUDED.date,
                name = EXCLUDED.name,
                amount = EXCLUDED.amount,
                category = EXCLUDED.category,
                category_detailed = EXCLUDED.category_detailed,
                pending = EXCLUDED.pending
            RETURNING (xmax = 0)
        """, (txn['transaction_id'], db_account_id, txn['date'], txn['name'],
              txn['amount'], category_of(txn), detailed_category_of(txn), txn['pending']))
        if cur.fetchone()[0]:
            inserted += 1
        else:
            updated += 1

    # Pending transactions get removed when their posted version arrives
    removed_ids = [r['transaction_id'] for r in removed]
    if removed_ids:
        cur.execute("DELETE FROM transactions WHERE plaid_transaction_id = ANY(%s)", (removed_ids,))

    # Save the cursor only after every page has been applied
    cur.execute("UPDATE items SET sync_cursor = %s WHERE id = %s", (new_cursor, db_item_id))

    print(f"  {inserted} new, {updated} updated, {len(removed_ids)} removed"
          + (f", {skipped} skipped (unknown account)" if skipped else ""))

    # Pending rows normally get a `removed` within days. Old ones are usually
    # orphans (Plaid will never remove them) and double count against the
    # posted version. Report only; deleting is a manual decision.
    cur.execute("""
        SELECT count(*) FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE a.item_id = %s AND t.pending AND t.date < CURRENT_DATE - %s
    """, (db_item_id, STALE_PENDING_DAYS))
    stale = cur.fetchone()[0]
    if stale:
        print(f"  ⚠️  {stale} pending transaction(s) older than {STALE_PENDING_DAYS} days; "
              f"likely orphans that double count. Check before trusting totals.")


conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()

# Plaid errors that mean the bank connection itself needs the user, not a retry.
RELINK_ERRORS = {
    'ITEM_LOGIN_REQUIRED': 'the bank login expired or the password changed',
    'PENDING_EXPIRATION': 'the bank connection is about to expire',
    'PENDING_DISCONNECT': 'the bank connection is about to disconnect',
    'INVALID_CREDENTIALS': 'the saved bank credentials are no longer valid',
    'ITEM_LOCKED': 'the bank account is locked',
    'USER_SETUP_REQUIRED': 'the bank needs you to finish a setup step on its website',
    'INVALID_MFA': 'the bank needs a new multi-factor login',
}


def plaid_error_code(e):
    try:
        return json.loads(e.body).get('error_code', 'UNKNOWN')
    except (TypeError, ValueError, AttributeError):
        return 'UNKNOWN'


failures = []
synced = []
cur.execute("SELECT id, access_token, institution_name, sync_cursor FROM items ORDER BY id")
for db_item_id, access_token, institution_name, sync_cursor in cur.fetchall():
    print(f"Syncing {institution_name}...")
    try:
        sync_item(cur, db_item_id, access_token, institution_name, sync_cursor)
        conn.commit()
        synced.append(institution_name)
    except ApiException as e:
        conn.rollback()  # nothing from this item is saved; its cursor is unchanged
        code = plaid_error_code(e)
        failures.append((institution_name, code))
        print(f"  FAILED: {code}")

cur.close()
conn.close()

if failures:
    bar = '!' * 72
    print(f"\n{bar}\nSYNC FAILED. Do not trust the report until this is fixed.\n")
    for institution, code in failures:
        reason = RELINK_ERRORS.get(code)
        print(f"  {institution}: {code}")
        if reason:
            print(f"    Why: {reason}.")
            print(f"    Fix: re-authenticate {institution} with Plaid Link in update mode")
            print(f"         (~/Repos/plaid-quickstart; create the link token with this item's")
            print(f"         existing access token). The access token does not change, so")
            print(f"         nothing in the database needs editing. Then rerun this sync.")
        else:
            print(f"    Not a login problem. Look up {code} in Plaid's error docs, then rerun.")
    print(f"\nSynced normally: {', '.join(synced) if synced else 'none'}\n{bar}")
    sys.exit(1)

print("Sync complete.")