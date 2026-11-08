"""Status report for one credit card's billing cycle, for someone who can't log
in to the card. Sent weekly and the day after each cycle closes (scheduler.py).

    python card_report.py                 # current cycle, prints the email text
    python card_report.py --closed        # the cycle that just closed

Cards are configured under [[card_reports]] in household.toml.
"""
import html
import os
import re
import sys
from datetime import date, timedelta

from household import CONFIG
from queries import get_connection, MERCHANT_SQL

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')

# A credit on a card is a payment if Plaid tagged it as income, a loan payment
# or a transfer (it labels autopay "INCOME"), or its name says so. Anything
# else is a return or refund.
PAYMENT_SQL = ("(t.category IN ('INCOME', 'LOAN_PAYMENTS', 'TRANSFER_IN') "
               "OR t.name ILIKE '%%PAYMENT%%THANK%%')")

# Friendlier names for descriptors that carry order codes.
FRIENDLY = (('AMAZON MKTPL', 'Amazon Marketplace'), ('AMAZON.COM', 'Amazon.com'),
            ('AMZN', 'Amazon'), ('KINDLE', 'Kindle'), ('PRIME VIDEO', 'Prime Video'),
            ('AUDIBLE', 'Audible'))


def friendly(name):
    up = name.upper()
    for prefix, label in FRIENDLY:
        if up.startswith(prefix):
            return label
    return re.sub(r'\*.*$', '', name).strip()


def money(x):
    return f"${x:,.2f}"


def nice(d):
    return f"{d:%b} {d.day}"


def _on_day(year, month, day):
    """date(year, month, day), clamped to the month's last day."""
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    return min(date(year, month, 1) + timedelta(days=day - 1), first_next - timedelta(days=1))


def cycle_bounds(day, start_day):
    """(first day, day after last) of the billing cycle containing `day`, for
    a card whose cycle starts on `start_day` each month."""
    this = _on_day(day.year, day.month, start_day)
    if day >= this:
        start = this
    else:
        prev = day.replace(day=1) - timedelta(days=1)
        start = _on_day(prev.year, prev.month, start_day)
    nxt = start.replace(day=1) + timedelta(days=32)
    return start, _on_day(nxt.year, nxt.month, start_day)


def previous_cycle(start, start_day):
    return cycle_bounds(start - timedelta(days=1), start_day)


def cycle_activity(cur, account_id, start, end):
    """Purchases, returns and payments on the card in [start, end)."""
    cur.execute(f"""
        SELECT t.date, {MERCHANT_SQL.replace('name', 't.name')}, t.amount, t.pending,
               (t.amount < 0 AND {PAYMENT_SQL}) AS is_payment
        FROM transactions t
        WHERE t.account_id = %s AND t.date >= %s AND t.date < %s
        ORDER BY t.date DESC, t.amount DESC
    """, (account_id, start, end))
    purchases, returns, payments = [], [], []
    for d, name, amount, pending, is_payment in cur.fetchall():
        item = (d, friendly(name), float(amount), pending)
        if is_payment:
            payments.append(item)
        elif amount > 0:
            purchases.append(item)
        else:
            returns.append(item)
    spent = sum(a for _, _, a, _ in purchases)
    returned = -sum(a for _, _, a, _ in returns)
    return {'start': start, 'end': end, 'purchases': purchases, 'returns': returns,
            'payments': payments, 'spent': spent, 'returned': returned, 'net': spent - returned}


def build(card, today, closed=False):
    """Everything the card email says, as data."""
    start_day = card['cycle_start_day']
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM accounts WHERE mask = %s AND account_type = 'credit'",
                        (card['account_mask'],))
            row = cur.fetchone()
            if not row:
                raise SystemExit(f"No credit account ending {card['account_mask']} for {card['name']}")
            account_id = row[0]
            start, end = cycle_bounds(today, start_day)
            if closed:
                start, end = previous_cycle(start, start_day)
            cycle = cycle_activity(cur, account_id, start, end)
            history = []
            s = start
            for _ in range(3):
                s, e = previous_cycle(s, start_day)
                history.append(cycle_activity(cur, account_id, s, e))
            cur.execute("""
                SELECT current, credit_limit, recorded_at FROM account_balances
                WHERE account_id = %s ORDER BY recorded_at DESC LIMIT 1
            """, (account_id,))
            bal = cur.fetchone()
            cur.execute(f"""
                SELECT max(t.date) FROM transactions t WHERE t.account_id = %s
            """, (account_id,))
            newest = cur.fetchone()[0]
    finally:
        conn.close()
    days_total = (end - start).days
    data_through = min(today - timedelta(days=1), end - timedelta(days=1))
    return {
        'card': card['name'], 'closed': closed, 'cycle': cycle, 'history': history,
        'day_of_cycle': min((today - start).days + 1, days_total), 'days_total': days_total,
        'balance': float(bal[0]) if bal and bal[0] is not None else None,
        'limit': float(bal[1]) if bal and bal[1] is not None else None,
        'balance_as_of': bal[2].date() if bal else None,
        'newest': newest, 'data_through': data_through,
    }


def _avg(history):
    full = [h['net'] for h in history if h['purchases'] or h['returns']]
    return sum(full) / len(full) if full else None


def text(r):
    c = r['cycle']
    last_day = c['end'] - timedelta(days=1)
    span = f"{nice(c['start'])} – {nice(last_day)}"
    n = len(c['purchases'])
    out = []
    if r['closed']:
        out += [f"{r['card'].upper()}: CYCLE CLOSED ({span})", '',
                f"This cycle came to {money(c['net'])} across {n} purchase{'s' if n != 1 else ''}"
                + (f", after {money(c['returned'])} in returns." if c['returned'] else ".")]
    else:
        out += [f"{r['card'].upper()}: THIS CYCLE SO FAR ({span})", '',
                f"Spent so far: {money(c['net'])} across {n} purchase{'s' if n != 1 else ''}"
                + (f", after {money(c['returned'])} in returns" if c['returned'] else "")
                + f". Day {r['day_of_cycle']} of {r['days_total']}."]
    if r['balance'] is not None:
        line = f"Balance on the card (per the bank, as of {nice(r['balance_as_of'])}): {money(r['balance'])}."
        prev_net = r['history'][0]['net']
        # Explain the balance when it is exactly last statement + this cycle
        # (the last statement hasn't been paid yet).
        if not r['closed'] and abs(r['balance'] - (prev_net + c['net'])) < 0.01 and prev_net > 0:
            line += (f" That is last cycle's {money(prev_net)}, which gets paid automatically, plus "
                     f"this cycle's {money(c['net'])}.")
        elif not r['closed'] and abs(r['balance'] - c['net']) < 0.01:
            line += " Last cycle is already paid, so this is all from this cycle."
        out.append(line)
    prev = r['history'][0]
    if prev['purchases'] or prev['returns']:
        out.append(f"Last cycle ({nice(prev['start'])} – {nice(prev['end'] - timedelta(days=1))}): "
                   f"{money(prev['net'])}.")
    avg = _avg(r['history'])
    if avg is not None:
        out.append(f"Average of the last {sum(1 for h in r['history'] if h['purchases'] or h['returns'])} "
                   f"cycles: {money(avg)}.")
    if c['payments']:
        d, _, a, _ = c['payments'][0]
        out.append(f"Payment received {nice(d)}: {money(-a)} (pays off the previous statement).")

    out += ['', 'PURCHASES' if not c['returns'] else 'PURCHASES AND RETURNS']
    items = sorted(c['purchases'] + c['returns'], key=lambda x: (x[0], -x[2]), reverse=True)
    if not items:
        out.append('  None yet.')
    for d, name, a, pending in items:
        label = name if a > 0 else f"Return: {name}"
        out.append(f"  {nice(d):<7} {label:<28} {('−' if a < 0 else '') + money(abs(a)):>10}"
                   + ("  (pending)" if pending else ""))
    out += ['', "Notes: the bank doesn't say what was bought, so match these to the Amazon order history "
            "by date and amount. New charges can take a day or two to show up"
            + (f"; this includes everything through {nice(r['newest'])}." if r['newest'] else ".")]
    return '\n'.join(out)


def subject(r):
    c = r['cycle']
    if r['closed']:
        return f"{r['card']}: cycle closed at {money(c['net'])}"
    return f"{r['card']}: {money(c['net'])} so far this cycle"


def html_page(r):
    body = html.escape(text(r))
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(subject(r))}</title>"
            f"<style>body{{font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
            f"max-width:720px;margin:24px auto;padding:0 16px}}pre{{font:14px/1.5 ui-monospace,Menlo,monospace;"
            f"white-space:pre-wrap}}</style></head><body><pre>{body}</pre></body></html>")


def write(r):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    slug = re.sub(r'[^a-z0-9]+', '_', r['card'].lower()).strip('_')
    path = os.path.join(OUTPUT_DIR, f"card_{slug}.html")
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html_page(r))
    return path


def cards():
    return CONFIG.get('card_reports', [])


if __name__ == '__main__':
    today = date.fromisoformat(os.environ['REPORT_DATE']) if os.getenv('REPORT_DATE') else date.today()
    for card in cards():
        r = build(card, today, closed='--closed' in sys.argv)
        print(f"Subject: {subject(r)}\n")
        print(text(r))
        print(f"\n(written to {write(r)})\n")
