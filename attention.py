"""Things in the data that the hand-entered settings don't explain yet.

    python attention.py   # print the list

The report shows these in a "Needs attention" box in the details. Each item
says what to update. After dealing with them, move reviewed_through in household.toml
forward so they stop showing.
"""
from datetime import date, timedelta

from queries import (
    get_connection, get_cash_flow, get_annual_bills, get_other_merchants,
    get_spend_by_class, event_for, month_periods, month_label,
    CURRENT_PAY, ONE_TIME_INCOME, PAYROLL_PATTERN, REVIEWED_THROUGH,
)

PAY_TOLERANCE = 0.05        # a paycheck this far from CURRENT_PAY is flagged
MISSING_PAY_DAYS = 20       # no paycheck from an employer for this long
STALE_SYNC_DAYS = 5         # newest transaction older than this
STALE_PENDING_DAYS = 14
OTHER_LIMIT = 150           # 'Other' category total per month


def money(x):
    return f"${x:,.2f}"


def _one_time_dates():
    return [(p, a, b) for _, p, a, b in ONE_TIME_INCOME]


def check_sync(cur, today):
    cur.execute("SELECT max(date) FROM transactions")
    newest = cur.fetchone()[0]
    if newest is None:
        return [("No transactions in the database.", "Run sync_transactions.py.")]
    if (today - newest).days > STALE_SYNC_DAYS:
        return [(f"Newest transaction is from {newest:%b} {newest.day}, {(today - newest).days} days ago.",
                 "Run sync_transactions.py; if it fails, follow its message.")]
    return []


def check_pending(cur, today):
    cur.execute("SELECT count(*), min(date) FROM transactions WHERE pending AND date < %s",
                (today - timedelta(days=STALE_PENDING_DAYS),))
    n, oldest = cur.fetchone()
    if n:
        return [(f"{n} pending transaction(s) older than {STALE_PENDING_DAYS} days (oldest {oldest}).",
                 "They are probably orphans that double count. Check them before trusting totals.")]
    return []


def check_pay(cur, today):
    items = []
    for label, expected, _, pattern in CURRENT_PAY:
        cur.execute("""
            SELECT t.date, -SUM(t.amount) FROM transactions t JOIN accounts a ON a.id = t.account_id
            WHERE a.account_type = 'depository' AND t.amount < 0 AND t.name ILIKE %s
            GROUP BY t.date ORDER BY t.date
        """, (pattern,))
        rows = [(d, float(a)) for d, a in cur.fetchall()]
        one_time = [(d, a) for d, a in rows
                    if any(d >= lo and d <= hi for p, lo, hi in _one_time_dates())]
        rows = [r for r in rows if r not in one_time]
        if not rows:
            items.append((f"{label}: no paychecks found.", "Check the pattern in [[pay.current]] in household.toml."))
            continue
        last_date = rows[-1][0]
        if (today - last_date).days > MISSING_PAY_DAYS:
            items.append((f"{label}: no paycheck since {last_date:%b} {last_date.day}.",
                          "If pay stopped or moved, update [[pay.current]] in household.toml."))
        for d, amt in rows:
            if d <= REVIEWED_THROUGH:
                continue
            if abs(amt - expected) / expected > PAY_TOLERANCE:
                items.append((f"{label}: {money(amt)} on {d:%b} {d.day}, expected about {money(expected)}.",
                              "If pay changed, update [[pay.current]] in household.toml. If it was a one-off "
                              "(bonus, payout), add it to [[one_time_income]]."))
    # Paychecks from an employer CURRENT_PAY doesn't know.
    known = ' AND '.join(f"t.name NOT ILIKE %s" for _ in CURRENT_PAY)
    cur.execute(f"""
        SELECT t.date, t.name, -t.amount FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE a.account_type = 'depository' AND t.amount < 0 AND t.name ILIKE %s
          AND t.date > %s AND {known}
        ORDER BY t.date
    """, (PAYROLL_PATTERN, REVIEWED_THROUGH) + tuple(p for *_, p in CURRENT_PAY))
    for d, name, amt in cur.fetchall():
        if any(d >= lo and d <= hi for p, lo, hi in _one_time_dates()):
            continue
        items.append((f"Paycheck from an employer not in the settings: {money(float(amt))} on {d:%b} {d.day}.",
                      "Add it to [[pay.current]] in household.toml, or to [[one_time_income]] if it was a one-off."))
    return items


def check_large_items(cur, today):
    """Large items after REVIEWED_THROUGH that are not annual bills, linked
    events or one-time income."""
    start = REVIEWED_THROUGH + timedelta(days=1)
    if start >= today:
        return []
    cf = get_cash_flow(cur, start, today)
    one_time_labels = {label for label, *_ in ONE_TIME_INCOME}
    items = []
    candidates = ([(d, n, a) for d, n, a in cf['large_in_items'] if n not in one_time_labels]
                  + [(d, n, -a) for d, n, a in cf['one_off_items']
                     if (d, n, a) not in cf['annual_bill_items']]
                  + [(d, n, -a) for d, n, a in cf['large_out_items']])
    for d, n, a in sorted(candidates):
        if event_for(d, n, a):
            continue
        items.append((f"Large item: {n} {'+' if a > 0 else '−'}{money(abs(a))} on {d:%b} {d.day}.",
                      "Fine as a one-off. If it pairs with another item (a trip paid back, a sale), add it "
                      "to [[linked_events]]; if it is pay, to [[one_time_income]]; if it is a yearly bill, "
                      "to [[annual_bills]]."))
    return items


def check_other(cur, today):
    items = []
    start, end, _, _ = month_periods(today)
    for s, e, name in ((start, end, month_label(start)), (end, today, 'this month so far')):
        if s >= e:
            continue
        rows = get_other_merchants(cur, s, e)
        total = sum(a for _, _, a, _ in rows)
        if total > OTHER_LIMIT:
            top = ', '.join(f"{m} {money(a)}" for m, _, a, _ in rows[:4])
            items.append((f"{money(total)} landed in 'Other' for {name}: {top}.",
                          "Add merchant rules to merchant_rules in household.toml."))
    return items


def check_bills(cur, today):
    items = []
    for b in get_annual_bills(cur, today):
        if b['overdue']:
            items.append((f"{b['label']} was due in {b['next_due']:%B %Y} but no payment was found.",
                          "Check whether it was paid another way; update [[annual_bills]] if the "
                          "payee or schedule changed."))
    start, end, prev_start, prev_end = month_periods(today)
    now = {c: dict(items_) for c, _, items_ in get_spend_by_class(cur, start, end)}
    before = {c: dict(items_) for c, _, items_ in get_spend_by_class(cur, prev_start, prev_end)}
    for cls, bills in now.items():
        for label, amt in bills.items():
            if amt == 0 and before.get(cls, {}).get(label, 0) > 0:
                items.append((f"No {label} charge in {month_label(start)} (there was one in "
                              f"{month_label(prev_start)}).",
                              "Check it was paid. If it was cancelled or the payee changed, update "
                              "[[expense_classes]] in household.toml."))
    return items


def get_attention(cur, today=None):
    """[(what, what to do)], most urgent first."""
    today = today or date.today()
    out = []
    for check in (check_sync, check_pending, check_pay, check_bills, check_large_items, check_other):
        out += check(cur, today)
    return out


if __name__ == '__main__':
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            found = get_attention(cur)
    finally:
        conn.close()
    if not found:
        print(f"Nothing needs attention (settings reviewed through {REVIEWED_THROUGH}).")
    for what, todo in found:
        print(f"- {what}\n    {todo}")
