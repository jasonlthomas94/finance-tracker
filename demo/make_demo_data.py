"""Builds a demo database with synthetic transactions (no real data) that
matches household.example.toml, so the report can be shown without real
finances.

    createdb finance_tracker_demo
    python demo/make_demo_data.py postgresql:///finance_tracker_demo
    DATABASE_URL=postgresql:///finance_tracker_demo HOUSEHOLD_CONFIG=household.example.toml \\
        REPORT_DATE=2026-07-08 python generate_report.py --out output/demo_report.html

It drops and recreates the three tables in the target database, so only
point it at a database made for the demo.
"""
import os
import random
import sys
from datetime import date, timedelta

import psycopg2

SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'schema.sql')
START, END = date(2026, 1, 1), date(2026, 7, 8)   # data through July 7

rng = random.Random(42)
rows = []          # (account key, date, name, amount, primary, detailed)


PRIMARIES = ('FOOD_AND_DRINK', 'GENERAL_MERCHANDISE', 'GENERAL_SERVICES', 'LOAN_PAYMENTS',
             'RENT_AND_UTILITIES', 'PERSONAL_CARE', 'TRANSFER_IN', 'TRANSFER_OUT', 'HOME_IMPROVEMENT',
             'GOVERNMENT_AND_NON_PROFIT', 'INCOME', 'TRANSPORTATION', 'ENTERTAINMENT', 'MEDICAL', 'TRAVEL')


def add(acct, d, name, amount, detailed):
    """One synthetic transaction; Plaid's primary category is the prefix of the detailed one."""
    primary = next(p for p in PRIMARIES if detailed.startswith(p))
    rows.append((acct, d, name, round(amount, 2), primary, detailed))


def months():
    d = START
    while d < END:
        yield d
        d = (d + timedelta(days=32)).replace(day=1)


def day(m, n):
    last = ((m + timedelta(days=32)).replace(day=1) - timedelta(days=1)).day
    d = m.replace(day=min(n, last))
    return d if d < END else None


def spread(m, count, lo, hi, acct, names, detailed):
    for _ in range(count):
        d = day(m, rng.randint(1, 28))
        if d:
            add(acct, d, rng.choice(names), rng.uniform(lo, hi), detailed)


for m in months():
    card_total = 0.0
    # Pay: Alex on the 15th and last day, Sam on the 7th and 21st.
    for n in (15, 31):
        if (d := day(m, n)):
            add('cu_chk', d, 'ACME CORP PAYROLL DIRECT DEP', -3200.00, 'INCOME_WAGES')
    for n in (7, 21):
        if (d := day(m, n)):
            add('bank_chk', d, 'GLOBEX PAYROLL', -2100.00, 'INCOME_WAGES')
    # Fixed bills.
    if (d := day(m, 1)):
        add('cu_chk', d, 'HOME LOAN SERVICING MTG PMT', 2150.00, 'LOAN_PAYMENTS_MORTGAGE_PAYMENT')
    if (d := day(m, 5)):
        add('cu_chk', d, 'CITY CU AUTO LOAN PMT', 465.00, 'LOAN_PAYMENTS_CAR_PAYMENT')
    for n in (2, 9, 16, 23):
        if (d := day(m, n)):
            add('bank_chk', d, 'LITTLE SPROUTS ACADEMY', 265.00, 'GENERAL_SERVICES_CHILDCARE')
    if (d := day(m, 12)):
        add('cu_chk', d, 'CITY ELECTRIC UTILITY', rng.uniform(110, 190), 'RENT_AND_UTILITIES_GAS_AND_ELECTRICITY')
    if (d := day(m, 18)):
        add('cu_chk', d, 'FIBERNET INTERNET', 74.99, 'RENT_AND_UTILITIES_INTERNET_AND_CABLE')
    if (d := day(m, 20)):
        add('cu_chk', d, 'TERM LIFE PREMIUM', 38.50, 'GENERAL_SERVICES_INSURANCE')
    for name, amt, n, det in (('IRON GYM MEMBERSHIP', 59.00, 3, 'PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS'),
                              ('STREAMFLIX', 17.99, 11, 'ENTERTAINMENT_TV_AND_MOVIES'),
                              ('TUNEBOX MUSIC', 10.99, 14, 'ENTERTAINMENT_MUSIC_AND_AUDIO')):
        if (d := day(m, n)):
            add('card1', d, name, amt, det)
            card_total += amt
    # Everyday spending, mostly on cards.
    before = len(rows)
    spread(m, rng.randint(6, 9), 45, 150, 'card1', ['FRESH MARKET #12', 'FRESH MARKET #40'], 'FOOD_AND_DRINK_GROCERIES')
    spread(m, rng.randint(2, 3), 110, 260, 'card1', ['COSTCO WHSE #0101'], 'GENERAL_MERCHANDISE_SUPERSTORES')
    spread(m, rng.randint(2, 3), 40, 60, 'card2', ['COSTCO GAS #0101', 'QUICKFUEL 22'], 'TRANSPORTATION_GAS')
    spread(m, rng.randint(4, 7), 22, 75, 'card2', ['CORNER BISTRO', 'TACO SPOT', 'PIZZA PALACE'],
           'FOOD_AND_DRINK_RESTAURANT')
    spread(m, rng.randint(6, 10), 4.5, 8.5, 'card2', ['BEAN THERE COFFEE'], 'FOOD_AND_DRINK_COFFEE')
    spread(m, rng.randint(3, 6), 15, 90, 'card1', ['AMAZON MKTPL*A1B2C', 'AMAZON MKTPL*D3E4F'],
           'GENERAL_MERCHANDISE_ONLINE_MARKETPLACES')
    spread(m, rng.randint(1, 3), 30, 110, 'card2', ['TARGET T-0412'], 'GENERAL_MERCHANDISE_SUPERSTORES')
    spread(m, rng.randint(0, 2), 25, 140, 'card2', ['NEIGHBORHOOD VET'], 'MEDICAL_VETERINARY_SERVICES')
    spread(m, rng.randint(0, 2), 20, 120, 'card1', ['MAIN ST PHARMACY'], 'MEDICAL_PHARMACIES_AND_SUPPLEMENTS')
    spread(m, rng.randint(0, 2), 15, 95, 'card2', ['HARDWARE HUT'], 'HOME_IMPROVEMENT_HARDWARE')
    card_total += sum(r[3] for r in rows[before:] if r[0].startswith('card'))
    # Pay the cards from checking early next month (or this month for the last one).
    pay_day = day((m + timedelta(days=32)).replace(day=1), 8) if m.month < 6 else None
    if pay_day:
        add('cu_chk', pay_day, 'EXAMPLE BANK CARD PMT AUTOPAY', round(card_total, 2), 'LOAN_PAYMENTS_CREDIT_CARD_PAYMENT')
        add('card1', pay_day, 'PAYMENT THANK YOU', -round(card_total, 2), 'LOAN_PAYMENTS_CREDIT_CARD_PAYMENT')
    # Savings: a deposit each month and a 529 contribution.
    if (d := day(m, 25)):
        add('bank_chk', d, 'ONLINE TRANSFER TO HIGH YIELD SAVINGS', rng.choice([800, 1000, 1200, 1500]),
            'TRANSFER_OUT_SAVINGS')
    if (d := day(m, 27)):
        add('bank_chk', d, 'COLLEGE 529 PLAN CONTRIB', 100.00, 'TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS')
    if (d := day(m, 10)):
        add('cu_chk', d, 'ONLINE TRANSFER CHECKING 2222', 500.00, 'TRANSFER_OUT_ACCOUNT_TRANSFER')
        add('bank_chk', d, 'ONLINE TRANSFER CHECKING 1111', -500.00, 'TRANSFER_IN_ACCOUNT_TRANSFER')

# One-offs that exercise the report.
add('cu_chk', date(2026, 3, 16), 'COUNTY TAX COLLECTOR PROPERTY TAX', 3240.00, 'GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT')
add('cu_chk', date(2026, 3, 20), 'ACME CORP PAYROLL BONUS', -4000.00, 'INCOME_WAGES')
add('card2', date(2026, 4, 11), 'SEASIDE RESORT LODGING', 1650.00, 'TRAVEL_LODGING')
add('card2', date(2026, 4, 11), 'SEASIDE RESORT LODGING', 1650.00, 'TRAVEL_LODGING')
add('card2', date(2026, 4, 13), 'SEASIDE RESORT DINING', 240.00, 'FOOD_AND_DRINK_RESTAURANT')
add('bank_chk', date(2026, 4, 14), 'Venmo CASHOUT', -1650.00, 'TRANSFER_IN_ACCOUNT_TRANSFER')
add('bank_chk', date(2026, 4, 15), 'Venmo CASHOUT', -1200.00, 'TRANSFER_IN_ACCOUNT_TRANSFER')
add('cu_chk', date(2026, 5, 6), 'SAFEGUARD INS ANNUAL PREMIUM', 2460.00, 'GENERAL_SERVICES_INSURANCE')
add('cu_chk', date(2026, 5, 5), 'FROM HIGH YIELD SAVINGS', -3000.00, 'TRANSFER_IN_SAVINGS')
add('card1', date(2026, 6, 9), 'STATE DMV REGISTRATION', 182.00, 'GOVERNMENT_AND_NON_PROFIT_GOVERNMENT_DEPARTMENTS_AND_AGENCIES')
add('card1', date(2026, 6, 22), 'APPLIANCE OUTLET', 1890.00, 'GENERAL_MERCHANDISE_ELECTRONICS')


def main(url):
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS transactions, accounts, items CASCADE")
    with open(SCHEMA) as f:
        cur.execute(f.read())
    items = {'cu': ('demo-item-cu', 'Example Credit Union'), 'bank': ('demo-item-bank', 'Example Bank')}
    item_ids = {}
    for key, (pid, name) in items.items():
        cur.execute("INSERT INTO items (plaid_item_id, access_token, institution_name, sync_cursor) "
                    "VALUES (%s, 'demo-not-a-token', %s, 'demo') RETURNING id", (pid, name))
        item_ids[key] = cur.fetchone()[0]
    accounts = {'cu_chk': ('cu', 'Checking', 'depository', 'checking', '1111'),
                'bank_chk': ('bank', 'Checking', 'depository', 'checking', '2222'),
                'card1': ('bank', 'Rewards Card', 'credit', 'credit card', '3333'),
                'card2': ('bank', 'Cash Back Card', 'credit', 'credit card', '4444')}
    acct_ids = {}
    for key, (item, name, typ, sub, mask) in accounts.items():
        cur.execute("INSERT INTO accounts (item_id, plaid_account_id, name, account_type, account_subtype, mask) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (item_ids[item], f'demo-{key}', name, typ, sub, mask))
        acct_ids[key] = cur.fetchone()[0]
    for i, (acct, d, name, amount, primary, detailed) in enumerate(rows):
        cur.execute("INSERT INTO transactions (plaid_transaction_id, account_id, date, name, amount, category, "
                    "category_detailed, pending) VALUES (%s, %s, %s, %s, %s, %s, %s, false)",
                    (f'demo-{i}', acct_ids[acct], d, name, amount, primary, detailed))
    conn.commit()
    print(f"Demo database ready: {len(rows)} synthetic transactions, {START} to {END - timedelta(days=1)}")


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'postgresql:///finance_tracker_demo')
