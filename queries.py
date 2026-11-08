import os
import re
from dotenv import load_dotenv
import psycopg2
from datetime import date, timedelta

load_dotenv()


def get_connection():
    return psycopg2.connect(os.getenv('DATABASE_URL'))


# Personal settings come from household.toml (see household.py and
# household.example.toml). Everything below turns them into the shapes the
# queries use; the meaning of each is documented in the example file.
from household import CONFIG as _C

# Categories that are never spending: money moving between your own accounts.
TRANSFER_CATEGORIES = ('TRANSFER_OUT', 'TRANSFER_IN')

# Payments to cards we already track. The purchases are counted when they hit
# the card, so the payment itself would double count. Payments to cards we do
# NOT track stay in as spending, since that is all we can see.
TRACKED_CARD_PAYMENT_PATTERN = _C['cards']['tracked_payment_pattern']

# Savings accounts are out of scope: money to or from them is a savings
# transfer, never spending or income, whatever Plaid tagged it.
SAVINGS_PATTERNS = tuple(_C['savings']['patterns'])
EDUCATION_SAVINGS_PATTERNS = tuple(_C['savings'].get('contribution_patterns', ()))
SAVINGS_LABEL = _C['savings']['label']

# Fixed monthly bills (e.g. mortgage, car payment): always regular spend
# whatever Plaid tagged them, given a stable label, never one-offs or spikes.
FIXED_BILLS = tuple((b['label'], b['pattern']) for b in _C.get('fixed_bills', ()))

# Expense classes, applied to regular spend (everything else is 'Variable').
EXPENSE_CLASSES = tuple((c['name'], tuple(tuple(i) for i in c['items'])) for c in _C.get('expense_classes', ()))
VARIABLE_CLASS = 'Variable'
ALL_FIXED_PATTERNS = tuple(p for _, items in EXPENSE_CLASSES for _, p in items)

# Bills paid once or twice a year: (label, pattern, minimum amount, months due).
# Kept out of regular spend at any size, listed with large one-time items, and
# used for the monthly set-aside and next-due figures. The minimum keeps small
# unrelated charges from the same payee out.
KNOWN_LARGE_BILLS = tuple((b['label'], b['pattern'], b.get('min_amount', 0), tuple(b['months']))
                          for b in _C.get('annual_bills', ()))

# Cash flow (checking accounts only: card-side credits are payments or refunds,
# never income).
PAYROLL_PATTERN = _C['pay'].get('payroll_pattern', '%PAYROLL%')
# Paychecks that were one-time money (payouts, bonuses): (label, pattern, first
# day, last day). Counted as large one-time money in, not normal pay.
ONE_TIME_INCOME = tuple((x['label'], x['pattern'], x['first'], x['last']) for x in _C.get('one_time_income', ()))
# Expected take-home at current rates: (label, per pay date, pay dates per
# month, payroll name pattern). Update when pay changes.
CURRENT_PAY = tuple((x['label'], x['per_pay_date'], x['pay_dates_per_month'], x['pattern'])
                    for x in _C['pay'].get('current', ()))
# The hand-entered settings were last checked against the data on this date;
# attention.py flags anything after it they don't explain.
REVIEWED_THROUGH = _C['general']['reviewed_through']
# Transfers between your own checking accounts.
OWN_TRANSFER_PATTERNS = tuple(_C.get('transfers', {}).get('own_patterns', ()))

# Anything at or above this amount gets pulled out and reported separately,
# regardless of category, so a large one-time purchase or payment never
# disappears silently just because it was tagged as a "transfer."
ONE_OFF_THRESHOLD = _C['general']['one_off_threshold']

# Earliest synced transaction; a month is reportable only if it starts after it.
HISTORY_START = _C['general']['history_start']


def _known_bill(t=''):
    return '(' + ' OR '.join(f"({t}name ILIKE {_lit(p)} AND {t}amount >= {int(m)})"
                             for _, p, m, _ in KNOWN_LARGE_BILLS) + ')'


def display_name(raw):
    """Label for a large item: a known bill's name, else the cleaned
    descriptor (same cleaning as MERCHANT_SQL, so no ACH noise or trace IDs)."""
    up = (raw or '').upper()
    for label, pattern, _, _ in KNOWN_LARGE_BILLS:
        if pattern.strip('%').upper() in up:
            return label
    n = re.sub(r'^ACH DEBIT\s+', '', raw or '', flags=re.I)
    n = re.sub(r'\s+(PURCHASE B|PPD|WEB)\b.*$', '', n)
    n = re.sub(r'\s+(@@|ST-).*$', '', n)
    n = re.sub(r'\s+[0-9]{6,}.*$', '', n)
    return n.strip()[:50]


def is_known_bill(raw):
    up = (raw or '').upper()
    return any(p.strip('%').upper() in up for _, p, _, _ in KNOWN_LARGE_BILLS)


def _one_time_income(t=''):
    if not ONE_TIME_INCOME:
        return 'FALSE'
    return '(' + ' OR '.join(f"({t}name ILIKE {_lit(p)} AND {t}date BETWEEN '{a}' AND '{b}')"
                             for _, p, a, b in ONE_TIME_INCOME) + ')'


def _one_time_label(t, otherwise):
    whens = ' '.join(f"WHEN {t}name ILIKE {_lit(p)} AND {t}date BETWEEN '{a}' AND '{b}' THEN "
                     + "'" + label.replace("'", "''") + "'"
                     for label, p, a, b in ONE_TIME_INCOME)
    return f"(CASE {whens} ELSE {otherwise} END)" if whens else otherwise


def expected_monthly_pay():
    return sum(amount * per_month for _, amount, per_month, _ in CURRENT_PAY)


def _lit(pattern):
    """Inline a constant LIKE pattern as an SQL literal. % is doubled because
    every query here also passes psycopg2 params."""
    return "'" + pattern.replace("'", "''").replace('%', '%%') + "'"


def _any(t, patterns):
    return '(' + ' OR '.join(f"{t}name ILIKE {_lit(p)}" for p in patterns) + ')'


def _savings(t=''):
    return _any(t, SAVINGS_PATTERNS)


def _fixed(t=''):
    return _any(t, [p for _, p in FIXED_BILLS])


def _fixed_label(t, otherwise, spaces=False):
    whens = ' '.join(f"WHEN {t}name ILIKE {_lit(p)} THEN '{label.replace('_', ' ') if spaces else label}'"
                     for label, p in FIXED_BILLS)
    return f"(CASE {whens} ELSE {otherwise} END)"


# Bank descriptors carry per-transaction noise (ACH prefixes, trace IDs, WEB ID
# numbers) that splits one merchant into many rows. Strip it so recurring
# merchants group together. Display-only: the raw name stays in the table.
MERCHANT_SQL = r"""btrim(
    regexp_replace(
      regexp_replace(
        regexp_replace(
          regexp_replace(name, '^ACH DEBIT\s+', '', 'i'),
        '\s+(PURCHASE B|PPD|WEB)\y.*$', ''),
      '\s+(@@|ST-).*$', ''),
    '\s+[0-9]{6,}.*$', '')
)"""

# Labels for grouping and display: fixed bills get one stable name (the
# mortgage descriptor carries a per-month date), everything else is cleaned.
MERCHANT_LABEL_SQL = _fixed_label('', MERCHANT_SQL, spaces=True)
CATEGORY_LABEL_SQL = _fixed_label('', 'category')


def month_periods(today):
    """Last complete calendar month and the month before it, as
    (cur_start, cur_end, prev_start, prev_end) with exclusive ends."""
    cur_end = today.replace(day=1)
    cur_start = (cur_end - timedelta(days=1)).replace(day=1)
    prev_end = cur_start
    prev_start = (prev_end - timedelta(days=1)).replace(day=1)
    return cur_start, cur_end, prev_start, prev_end


CATEGORY_NAMES = {'GOVERNMENT_AND_NON_PROFIT': 'Government and non-profit'}


def nice_category(c):
    """Display name for a category: GENERAL_SERVICES -> General services.
    Household categories ('Kids & daycare') are already display names."""
    if c and not c.isupper():
        return c
    return CATEGORY_NAMES.get(c) or (c or 'Uncategorized').replace('_', ' ').capitalize()


def month_label(d):
    return d.strftime('%B %Y')


def is_money_movement(name, category):
    """True for rows that move money between the user's own accounts or pay a
    tracked card: savings transfers, other transfers, and LOAN_PAYMENTS to
    tracked Chase cards. Fixed bills and other loan payments are spending."""
    n = (name or '').upper()
    if any(p.strip('%').upper() in n for p in SAVINGS_PATTERNS):
        return True
    if any(p.strip('%').upper() in n for _, p in FIXED_BILLS):
        return False
    if category in TRANSFER_CATEGORIES:
        return True
    return category == 'LOAN_PAYMENTS' and TRACKED_CARD_PAYMENT_PATTERN.strip('%') in n


def regular_filter(threshold, t='', exclude_fixed=False):
    """SQL fragment + params selecting 'regular spend' rows.
    `t` is an optional table alias prefix such as 't.' for joined queries.
    `exclude_fixed` drops every fixed expense in EXPENSE_CLASSES (for spike detection)."""
    sql = f"""
        {t}amount > 0
        AND NOT {_savings(t)}
        AND ({t}category IS NULL OR {t}category NOT IN ('TRANSFER_OUT', 'TRANSFER_IN') OR {_fixed(t)})
        AND NOT ({t}category = 'LOAN_PAYMENTS' AND {t}name ILIKE %s)
        AND ({t}amount < %s OR {_fixed(t)})
        AND NOT {_known_bill(t)}
    """
    if exclude_fixed:
        sql += f" AND NOT {_any(t, ALL_FIXED_PATTERNS)}"
    return sql, (TRACKED_CARD_PAYMENT_PATTERN, threshold)


def get_large_one_offs(cur, start, end, threshold=ONE_OFF_THRESHOLD):
    """Anything big, regardless of category -- nothing this size should be invisible --
    plus known annual bills of any size. Fixed monthly bills are not one-offs."""
    cur.execute(f"""
        SELECT name, amount, date, category
        FROM transactions
        WHERE date >= %s AND date < %s AND (amount >= %s OR {_known_bill()})
        AND NOT {_fixed()}
        ORDER BY amount DESC
    """, (start, end, threshold))
    return cur.fetchall()


def get_spend_by_category(cur, start, end, threshold=ONE_OFF_THRESHOLD):
    flt, params = regular_filter(threshold)
    cur.execute(f"""
        SELECT cat, SUM(amount) AS total
        FROM (
            SELECT {CATEGORY_LABEL_SQL} AS cat, amount
            FROM transactions
            WHERE date >= %s AND date < %s AND {flt}
        ) s
        GROUP BY cat
        ORDER BY total DESC
    """, (start, end) + params)
    return cur.fetchall()


def get_spend_by_account(cur, start, end, threshold=ONE_OFF_THRESHOLD):
    """LEFT JOIN so an idle account shows up at $0 instead of silently vanishing."""
    flt, params = regular_filter(threshold, t='t.')
    cur.execute(f"""
        SELECT
            COALESCE(a.display_name, a.official_name, a.name) || ' ...' || COALESCE(a.mask, '????') AS account_label,
            COALESCE(SUM(t.amount), 0) AS total
        FROM accounts a
        LEFT JOIN transactions t
            ON t.account_id = a.id
            AND t.date >= %s AND t.date < %s AND {flt}
        GROUP BY a.id
        ORDER BY total DESC
    """, (start, end) + params)
    return cur.fetchall()


def get_top_merchants(cur, start, end, threshold=ONE_OFF_THRESHOLD, limit=10):
    flt, params = regular_filter(threshold)
    cur.execute(f"""
        SELECT merchant, SUM(amount) AS total, COUNT(*) AS num_transactions
        FROM (
            SELECT {MERCHANT_LABEL_SQL} AS merchant, amount
            FROM transactions
            WHERE date >= %s AND date < %s AND {flt}
        ) cleaned
        GROUP BY merchant
        ORDER BY total DESC
        LIMIT %s
    """, (start, end) + params + (limit,))
    return cur.fetchall()


def get_total_spend(cur, start, end, threshold=ONE_OFF_THRESHOLD):
    flt, params = regular_filter(threshold)
    cur.execute(f"""
        SELECT COALESCE(SUM(amount), 0)
        FROM transactions
        WHERE date >= %s AND date < %s AND {flt}
    """, (start, end) + params)
    return cur.fetchone()[0]


def get_daily_spend(cur, start, end, threshold=ONE_OFF_THRESHOLD, exclude_fixed=False):
    """Regular spend grouped by day, for a trend line or spike detection."""
    flt, params = regular_filter(threshold, exclude_fixed=exclude_fixed)
    cur.execute(f"""
        SELECT date, SUM(amount) AS total
        FROM transactions
        WHERE date >= %s AND date < %s AND {flt}
        GROUP BY date
        ORDER BY date
    """, (start, end) + params)
    return cur.fetchall()


def get_largest_regular_charge(cur, day, threshold=ONE_OFF_THRESHOLD, exclude_fixed=False):
    """Largest regular-spend charge on a given day, with a cleaned merchant name."""
    flt, params = regular_filter(threshold, exclude_fixed=exclude_fixed)
    cur.execute(f"""
        SELECT {MERCHANT_LABEL_SQL} AS merchant, amount
        FROM transactions
        WHERE date = %s AND {flt}
        ORDER BY amount DESC
        LIMIT 1
    """, (day,) + params)
    return cur.fetchone()


def get_spend_by_class(cur, start, end, threshold=ONE_OFF_THRESHOLD):
    """Regular spend split into EXPENSE_CLASSES, with each fixed item's total.
    Returns [(class, total, [(item, total), ...]), ...] including Variable;
    classes and items with no spend are kept at 0 so a missed bill shows up."""
    flt, params = regular_filter(threshold)
    whens = ' '.join(f"WHEN name ILIKE {_lit(p)} THEN '{label}'"
                     for _, items in EXPENSE_CLASSES for label, p in items)
    cur.execute(f"""
        SELECT (CASE {whens} ELSE NULL END) AS item, SUM(amount)
        FROM transactions
        WHERE date >= %s AND date < %s AND {flt}
        GROUP BY 1
    """, (start, end) + params)
    totals = {item: float(a) for item, a in cur.fetchall()}
    result = []
    for cls, items in EXPENSE_CLASSES:
        rows = [(label, totals.get(label, 0.0)) for label, _ in items]
        result.append((cls, sum(a for _, a in rows), rows))
    result.append((VARIABLE_CLASS, totals.get(None, 0.0), []))
    return result


def get_cash_flow(cur, start, end, threshold=ONE_OFF_THRESHOLD):
    """Money in, money out and saved for a period. See CLAUDE.md for the rules.
    Inflows are positive numbers. Items of `threshold`+ that are not paychecks
    or regular spend are listed separately and included in the totals."""
    own = _any('t.', OWN_TRANSFER_PATTERNS)
    saved = _any('t.', SAVINGS_PATTERNS + EDUCATION_SAVINGS_PATTERNS)
    payroll = f"t.name ILIKE {_lit(PAYROLL_PATTERN)}"
    cur.execute(f"""
        SELECT bucket, SUM(amount), COUNT(*) FROM (
            SELECT t.amount, CASE
                WHEN {saved} AND t.amount > 0 THEN 'saved_in'
                WHEN {saved} THEN 'saved_out'
                WHEN t.amount < 0 AND {_one_time_income('t.')} THEN 'large_in'
                WHEN t.amount < 0 AND t.category = 'INCOME' AND {payroll} THEN 'paychecks'
                WHEN t.amount < 0 AND t.category IN ('INCOME', 'TRANSFER_IN') AND NOT {own}
                     AND t.amount <= -%s THEN 'large_in'
                WHEN t.amount < 0 AND t.category = 'INCOME' THEN 'other_income'
                WHEN t.amount < 0 AND t.category = 'TRANSFER_IN' AND NOT {own} THEN 'from_others'
                WHEN t.amount > 0 AND t.category = 'TRANSFER_OUT' AND NOT {own}
                     AND NOT {_fixed('t.')} THEN 'to_others'
            END AS bucket
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE a.account_type = 'depository' AND t.date >= %s AND t.date < %s
        ) s
        WHERE bucket IS NOT NULL
        GROUP BY bucket
    """, (threshold, start, end))
    b = {k: (float(v), n) for k, v, n in cur.fetchall()}

    cur.execute(f"""
        SELECT t.date, {_one_time_label('t.', MERCHANT_SQL.replace('name', 't.name'))}, -t.amount
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE a.account_type = 'depository' AND t.date >= %s AND t.date < %s
          AND t.amount < 0 AND NOT {saved} AND NOT {own}
          AND ({_one_time_income('t.')}
               OR (t.amount <= -%s AND t.category IN ('INCOME', 'TRANSFER_IN') AND NOT {payroll}))
        ORDER BY t.date
    """, (start, end, threshold))
    large_in = [(d, n.replace("''", "'"), float(a)) for d, n, a in cur.fetchall()]

    cur.execute(f"""
        SELECT t.date, {MERCHANT_SQL.replace('name', 't.name')}, t.amount
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE a.account_type = 'depository' AND t.date >= %s AND t.date < %s
          AND t.amount >= %s AND t.category = 'TRANSFER_OUT'
          AND NOT {saved} AND NOT {own} AND NOT {_fixed('t.')}
        ORDER BY t.date
    """, (start, end, threshold))
    large_out = [(d, n, float(a)) for d, n, a in cur.fetchall()]

    one_offs = [(d, display_name(n), float(a)) for n, a, d, c in get_large_one_offs(cur, start, end, threshold)
                if not is_money_movement(n, c)]
    regular = float(get_total_spend(cur, start, end, threshold))

    def inflow(k):
        return -b.get(k, (0.0, 0))[0]

    cf = {
        'paychecks': inflow('paychecks'),
        'paycheck_count': b.get('paychecks', (0, 0))[1],
        'other_income': inflow('other_income'),
        'from_others': inflow('from_others'),
        'large_in': sum(a for _, _, a in large_in),
        'large_in_items': large_in,
        'regular_spend': regular,
        'one_off_spend': sum(a for _, _, a in one_offs),
        'one_off_items': one_offs,
        'to_others': b.get('to_others', (0.0, 0))[0],
        'large_out_items': large_out,   # included in to_others
        'saved_in': b.get('saved_in', (0.0, 0))[0],      # Discover deposits + 529 contributions
        'saved_out': -b.get('saved_out', (0.0, 0))[0],   # Discover withdrawals (positive number)
    }
    cf['money_in'] = cf['paychecks'] + cf['other_income'] + cf['from_others'] + cf['large_in']
    cf['money_out'] = cf['regular_spend'] + cf['one_off_spend'] + cf['to_others']
    cf['net'] = cf['money_in'] - cf['money_out']
    cf['saved'] = cf['saved_in'] - cf['saved_out']   # net into savings; negative = pulled out
    # Net for an ordinary month: without large one-time items in either direction.
    cf['everyday_net'] = (cf['net'] - cf['large_in'] + cf['one_off_spend']
                          + sum(a for _, _, a in cf['large_out_items']))
    # Yearly bills paid this month (already inside one_off_spend).
    labels = {label for label, *_ in KNOWN_LARGE_BILLS}
    cf['annual_bill_items'] = [(d, n, a) for d, n, a in one_offs if n in labels]
    cf['annual_bills'] = sum(a for _, _, a in cf['annual_bill_items'])
    return cf


def get_monthly_overview(cur, today, months=6):
    """Cash flow for each of the last `months` complete calendar months that
    fall inside synced history, oldest first, plus the current month so far.
    Returns (rows, mtd) where each row is (month_start, cash_flow_dict)."""
    rows = []
    end = today.replace(day=1)
    while len(rows) < months:
        start = (end - timedelta(days=1)).replace(day=1)
        if start < HISTORY_START:
            break
        rows.append((start, get_cash_flow(cur, start, end)))
        end = start
    rows.reverse()
    mtd_start = today.replace(day=1)
    mtd = (mtd_start, get_cash_flow(cur, mtd_start, today)) if today > mtd_start else None
    return rows, mtd


def get_annual_bills(cur, today):
    """For each known annual bill: the last payment seen, its schedule, the
    monthly set-aside it needs (last amount x payments per year / 12), and the
    next due month. Bills never seen yet have amount None and set_aside 0."""
    out = []
    for label, pattern, minimum, months in KNOWN_LARGE_BILLS:
        cur.execute("""
            SELECT date, amount FROM transactions
            WHERE name ILIKE %s AND amount >= %s AND date < %s
            ORDER BY date DESC LIMIT 1
        """, (pattern, minimum, today))
        row = cur.fetchone()
        last_date, last_amt = (row[0], float(row[1])) if row else (None, None)
        # Next due: the first scheduled month after the last payment's month
        # (or from this month if never paid), so a bill already paid this
        # month points to its next occurrence.
        y, mth = ((last_date.year, last_date.month) if last_date
                  else (today.year, today.month - 1))
        for _ in range(24):
            mth += 1
            if mth > 12:
                y, mth = y + 1, 1
            if mth in months:
                break
        next_due = date(y, mth, 1)
        out.append({
            'label': label, 'months': months, 'last_date': last_date, 'last_amount': last_amt,
            'set_aside': (last_amt or 0.0) * len(months) / 12,
            'next_due': next_due,
            'overdue': next_due < today.replace(day=1),
        })
    return out



# One-time events whose ins and outs belong together (a car swap, a trip that
# friends paid back), shown as one line with the net. Each rule is
# (name pattern, first day, last day, 'out' or 'in'); totals cover every
# matching row in any account, not just large ones. Display only: the rows
# are still counted where they always were.
LINKED_EVENTS = tuple({'short': e['short'], 'label': e['label'], 'rules': tuple(tuple(r) for r in e['rules'])}
                      for e in _C.get('linked_events', ()))


def _rule_matches(rule, d, name, amount_in):
    pattern, first, last, direction = rule
    core = pattern.strip('%').upper()
    n = (name or '').upper()
    hit = n.startswith(core) if not pattern.startswith('%') else core in n
    return hit and first <= d <= last and (direction == 'in') == amount_in


def event_for(d, name, signed_amount):
    """The LINKED_EVENTS entry a large item belongs to, or None. signed_amount: + = in."""
    for ev in LINKED_EVENTS:
        if any(_rule_matches(r, d, name, signed_amount > 0) for r in ev['rules']):
            return ev
    return None


def get_linked_events(cur, start, end):
    """Events with any matching row in [start, end): [(short, label, out, in, net)]."""
    result = []
    for ev in LINKED_EVENTS:
        out_total = in_total = 0.0
        for pattern, first, last, direction in ev['rules']:
            lo, hi = max(first, start), min(last, end - timedelta(days=1))
            if lo > hi:
                continue
            cur.execute(f"""
                SELECT COALESCE(SUM(amount), 0) FROM transactions
                WHERE name ILIKE %s AND date >= %s AND date <= %s
                AND amount {'>' if direction == 'out' else '<'} 0
            """, (pattern, lo, hi))
            v = float(cur.fetchone()[0])
            if direction == 'out':
                out_total += v
            else:
                in_total += -v
        if out_total or in_total:
            result.append((ev['short'], ev['label'], out_total, in_total, in_total - out_total))
    return result


def get_forward_estimate(cur, months, set_aside):
    """What a normal month should leave going forward, built from current
    amounts: expected pay (CURRENT_PAY) minus fixed bills at their latest
    month's level, typical variable spending (average of `months`), typical
    small payments to other people, and the yearly-bill set-aside.
    `months` is [(month_start, cash_flow)] oldest first, complete months."""
    if not months:
        return None
    classes = []
    for m, _ in months:
        end = (m + timedelta(days=32)).replace(day=1)
        classes.append({c: a for c, a, _ in get_spend_by_class(cur, m, end)})
    latest = classes[-1]
    fixed = sum(a for c, a in latest.items() if c != VARIABLE_CLASS)
    variable = sum(c.get(VARIABLE_CLASS, 0.0) for c in classes) / len(classes)
    to_others = sum(cf['to_others'] - sum(a for _, _, a in cf['large_out_items'])
                    for _, cf in months) / len(months)
    pay = expected_monthly_pay()
    left = pay - fixed - variable - to_others - set_aside
    return {'pay': pay, 'fixed': fixed, 'fixed_month': months[-1][0], 'variable': variable,
            'to_others': to_others, 'set_aside': set_aside, 'left': left, 'n': len(months)}


# Household categories for "where the money went". First matching rule wins:
#   ('name', pattern)      - ILIKE on the raw transaction name (merchant rules)
#   ('detailed', prefix)   - Plaid's detailed category starts with this
#   ('primary', category)  - Plaid's broad category
# Anything unmatched is 'Other'; `get_other_merchants` lists those for review.
HOUSEHOLD_RULES = tuple((cat, 'name', pattern) for cat, pattern in _C.get('categories', {}).get('merchant_rules', ())) + (
    # Plaid detailed categories.
    ('Groceries', 'detailed', 'FOOD_AND_DRINK_GROCERIES'),
    ('Coffee', 'detailed', 'FOOD_AND_DRINK_COFFEE'),
    ('Restaurants & bars', 'detailed', 'FOOD_AND_DRINK_'),
    ('Kids & daycare', 'detailed', 'GENERAL_SERVICES_CHILDCARE'),
    ('Gas & convenience', 'detailed', 'TRANSPORTATION_GAS'),
    ('Gas & convenience', 'detailed', 'GENERAL_MERCHANDISE_CONVENIENCE_STORES'),
    ('Car', 'detailed', 'GENERAL_SERVICES_AUTOMOTIVE'),
    ('Car', 'detailed', 'TRANSPORTATION_PARKING'),
    ('Travel', 'detailed', 'TRAVEL_'),
    ('Travel', 'detailed', 'TRANSPORTATION_TAXIS'),
    ('Pets', 'detailed', 'MEDICAL_VETERINARY'),
    ('Health', 'detailed', 'MEDICAL_'),
    ('Fitness', 'detailed', 'PERSONAL_CARE_GYMS'),
    ('Personal care', 'detailed', 'PERSONAL_CARE_'),
    ('Utilities & internet', 'detailed', 'RENT_AND_UTILITIES_GAS'),
    ('Utilities & internet', 'detailed', 'RENT_AND_UTILITIES_OTHER_UTILITIES'),
    ('Utilities & internet', 'detailed', 'RENT_AND_UTILITIES_INTERNET'),
    ('Utilities & internet', 'detailed', 'RENT_AND_UTILITIES_WATER'),
    ('Utilities & internet', 'detailed', 'RENT_AND_UTILITIES_TELEPHONE'),
    ('Insurance', 'detailed', 'GENERAL_SERVICES_INSURANCE'),
    ('Subscriptions & streaming', 'detailed', 'ENTERTAINMENT_TV_AND_MOVIES'),
    ('Subscriptions & streaming', 'detailed', 'ENTERTAINMENT_MUSIC'),
    ('Fun & outings', 'detailed', 'ENTERTAINMENT_'),
    ('Home', 'detailed', 'HOME_IMPROVEMENT_'),
    ('Education', 'detailed', 'GENERAL_SERVICES_EDUCATION'),
    ('Giving', 'detailed', 'GOVERNMENT_AND_NON_PROFIT_DONATIONS'),
    ('Other card payments', 'detailed', 'LOAN_PAYMENTS_CREDIT_CARD'),
    ('Fees', 'detailed', 'BANK_FEES_'),
    ('Shopping', 'detailed', 'GENERAL_MERCHANDISE_'),
    # Broad fallbacks.
    ('Restaurants & bars', 'primary', 'FOOD_AND_DRINK'),
    ('Shopping', 'primary', 'GENERAL_MERCHANDISE'),
    ('Health', 'primary', 'MEDICAL'),
)
OTHER_HOUSEHOLD = 'Other'


def _household_sql(t=''):
    whens = []
    for label, kind, value in HOUSEHOLD_RULES:
        lab = "'" + label.replace("'", "''") + "'"
        if kind == 'name':
            whens.append(f"WHEN {t}name ILIKE {_lit(value)} THEN {lab}")
        elif kind == 'detailed':
            whens.append(f"WHEN {t}category_detailed LIKE {_lit(value + '%')} THEN {lab}")
        else:
            whens.append(f"WHEN {t}category = {_lit(value)} THEN {lab}")
    return f"(CASE {' '.join(whens)} ELSE '{OTHER_HOUSEHOLD}' END)"


HOUSEHOLD_SQL = _household_sql()


def get_spend_by_household(cur, start, end, threshold=ONE_OFF_THRESHOLD):
    """Regular spend by household category: [(category, total, count)], largest first."""
    flt, params = regular_filter(threshold)
    cur.execute(f"""
        SELECT {HOUSEHOLD_SQL} AS cat, SUM(amount), COUNT(*)
        FROM transactions
        WHERE date >= %s AND date < %s AND {flt}
        GROUP BY 1 ORDER BY 2 DESC
    """, (start, end) + params)
    return [(c, float(a), n) for c, a, n in cur.fetchall()]


def get_other_merchants(cur, start, end, threshold=ONE_OFF_THRESHOLD):
    """Merchants that landed in 'Other', for review: [(merchant, plaid detailed, total, count)]."""
    flt, params = regular_filter(threshold)
    cur.execute(f"""
        SELECT {MERCHANT_LABEL_SQL}, category_detailed, SUM(amount), COUNT(*)
        FROM transactions
        WHERE date >= %s AND date < %s AND {flt} AND {HOUSEHOLD_SQL} = '{OTHER_HOUSEHOLD}'
        GROUP BY 1, 2 ORDER BY 3 DESC
    """, (start, end) + params)
    return [(m, d, float(a), n) for m, d, a, n in cur.fetchall()]
