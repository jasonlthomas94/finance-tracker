import os
from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
import psycopg2

load_dotenv()

configuration = plaid.Configuration(
    host=plaid.Environment.Production,
    api_key={
        'clientId': os.getenv('PLAID_CLIENT_ID'),
        'secret': os.getenv('PLAID_SECRET'),
    }
)
api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)

# Paste your real access_token here temporarily just to run this once
ACCESS_TOKEN = "FAKE_TOKEN"

accounts_response = client.accounts_get(AccountsGetRequest(access_token=ACCESS_TOKEN))

conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()

cur.execute("SELECT id FROM items WHERE access_token = %s", (ACCESS_TOKEN,))
db_item_id = cur.fetchone()[0]

for acct in accounts_response['accounts']:
    cur.execute("""
        INSERT INTO accounts (item_id, plaid_account_id, name, official_name, account_type, account_subtype, mask)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (plaid_account_id) DO NOTHING
    """, (db_item_id, acct['account_id'], acct['name'], acct.get('official_name'),
          str(acct['type']), str(acct['subtype']), acct.get('mask')))

conn.commit()
print(f"Inserted {len(accounts_response['accounts'])} accounts")
cur.close()
conn.close()