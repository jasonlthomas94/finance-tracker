"""Automated checks. generate_report.py runs these first and refuses to write
the report if any fail.

    python checks.py   # run them on their own

Two kinds:
  - Data checks: on the real database, for every month the report covers,
    the different breakdowns of the same money must agree, and money that is
    never spending (savings, tracked card payments, transfers) must not leak in.
  - Rule checks: fixed, made-up inputs for logic that the current data does
    not exercise (every verdict, the opener checks, January month boundaries,
    labels). No database needed.
"""
import sys
from datetime import date, timedelta

TOL = 0.01


class Checks:
    def __init__(self):
        self.failures = []
        self.count = 0

    def ok(self, condition, message):
        self.count += 1
        if not condition:
            self.failures.append(message)

    def close(self, a, b, message):
        self.ok(abs(a - b) < TOL, f"{message}: {a:,.2f} vs {b:,.2f}")


# ---------------------------------------------------------------- data checks

def data_checks(c, cur, today):
    from queries import (
        get_monthly_overview, get_total_spend, get_spend_by_household, get_spend_by_class,
        get_spend_by_account, get_spend_by_category, get_large_one_offs, regular_filter,
        get_annual_bills, ONE_OFF_THRESHOLD, SAVINGS_PATTERNS, TRACKED_CARD_PAYMENT_PATTERN,
        KNOWN_LARGE_BILLS, FIXED_BILLS,
    )
    rows, mtd = get_monthly_overview(cur, today)
    c.ok(len(rows) > 0, "no complete months to report on")
    months = [(m, (m + timedelta(days=32)).replace(day=1)) for m, _ in rows]
    if mtd:
        months.append((mtd[0], today))
    flt, params = regular_filter(ONE_OFF_THRESHOLD)

    for (start, end), cf in zip(months, [cf for _, cf in rows] + ([mtd[1]] if mtd else [])):
        tag = f"{start:%b %Y}"
        total = float(get_total_spend(cur, start, end))
        # Every breakdown of regular spending adds up to the same total.
        c.close(sum(a for _, a, _ in get_spend_by_household(cur, start, end)), total,
                f"{tag}: household categories vs regular spend")
        c.close(sum(a for _, a, _ in get_spend_by_class(cur, start, end)), total,
                f"{tag}: fixed/variable classes vs regular spend")
        c.close(sum(float(a) for _, a in get_spend_by_account(cur, start, end)), total,
                f"{tag}: accounts vs regular spend")
        c.close(sum(float(a) for _, a in get_spend_by_category(cur, start, end)), total,
                f"{tag}: Plaid categories vs regular spend")
        for cls, amt, items in get_spend_by_class(cur, start, end):
            if items:
                c.close(sum(a for _, a in items), amt, f"{tag}: items within '{cls}'")

        # Cash flow adds up.
        c.close(cf['regular_spend'], total, f"{tag}: cash flow regular spend")
        c.close(cf['money_in'], cf['paychecks'] + cf['other_income'] + cf['from_others'] + cf['large_in'],
                f"{tag}: money in parts")
        c.close(cf['money_out'], cf['regular_spend'] + cf['one_off_spend'] + cf['to_others'],
                f"{tag}: money out parts")
        c.close(cf['net'], cf['money_in'] - cf['money_out'], f"{tag}: net")
        c.close(cf['saved'], cf['saved_in'] - cf['saved_out'], f"{tag}: saved")
        c.close(cf['large_in'], sum(a for _, _, a in cf['large_in_items']), f"{tag}: large-in list")
        c.close(cf['one_off_spend'], sum(a for _, _, a in cf['one_off_items']), f"{tag}: one-off list")
        c.ok(sum(a for _, _, a in cf['large_out_items']) <= cf['to_others'] + TOL,
             f"{tag}: large payments to others exceed the to-others total")

        # Nothing is both regular spend and a one-off.
        one_off_names = {(n, float(a), d) for n, a, d, _ in get_large_one_offs(cur, start, end)}
        cur.execute(f"SELECT name, amount, date FROM transactions WHERE date >= %s AND date < %s AND {flt}",
                    (start, end) + params)
        regular_rows = {(n, float(a), d) for n, a, d in cur.fetchall()}
        both = regular_rows & one_off_names
        c.ok(not both, f"{tag}: {len(both)} row(s) counted as both regular spend and one-off")

        # Money that is never spending stays out.
        for pattern, why in ([(p, 'savings') for p in SAVINGS_PATTERNS]
                             + [(TRACKED_CARD_PAYMENT_PATTERN, 'tracked card payment')]
                             + [(p, 'annual bill') for _, p, m, _ in KNOWN_LARGE_BILLS]):
            cur.execute(f"SELECT count(*) FROM transactions WHERE date >= %s AND date < %s AND {flt} "
                        f"AND name ILIKE %s" + (" AND amount >= 1000" if why == 'annual bill' else ""),
                        (start, end) + params + (pattern,))
            n = cur.fetchone()[0]
            c.ok(n == 0, f"{tag}: {n} {why} row(s) ({pattern}) counted as regular spend")
        cur.execute(f"SELECT count(*) FROM transactions WHERE date >= %s AND date < %s AND {flt} "
                    f"AND category IN ('TRANSFER_IN', 'TRANSFER_OUT') AND NOT ("
                    + ' OR '.join("name ILIKE %s" for _ in FIXED_BILLS) + ")",
                    (start, end) + params + tuple(p for _, p in FIXED_BILLS))
        n = cur.fetchone()[0]
        c.ok(n == 0, f"{tag}: {n} transfer row(s) counted as regular spend")

    # Card reports: every row in the cycle is a purchase, a return or a payment,
    # and the cycle total is purchases minus returns.
    from card_report import cards, cycle_bounds as cb, cycle_activity
    for card in cards():
        cur.execute("SELECT id FROM accounts WHERE mask = %s AND account_type = 'credit'", (card['account_mask'],))
        row = cur.fetchone()
        c.ok(row is not None, f"card account for {card['name']} exists")
        if not row:
            continue
        start, end = cb(today, card['cycle_start_day'])
        act = cycle_activity(cur, row[0], start, end)
        cur.execute("SELECT count(*), COALESCE(sum(amount), 0) FROM transactions WHERE account_id = %s "
                    "AND date >= %s AND date < %s", (row[0], start, end))
        n, total = cur.fetchone()
        c.ok(len(act['purchases']) + len(act['returns']) + len(act['payments']) == n,
             f"{card['name']}: every cycle row classified")
        c.close(act['spent'] - act['returned'] + sum(a for _, _, a, _ in act['payments']), float(total),
                f"{card['name']}: cycle rows add up")
        c.ok(all(a < 0 for _, _, a, _ in act['payments']), f"{card['name']}: payments are credits")

    # Whole database.
    cur.execute("SELECT count(*) FROM transactions WHERE account_id IS NULL")
    c.ok(cur.fetchone()[0] == 0, "transactions without an account")
    cur.execute("SELECT count(*) FROM transactions WHERE category_detailed IS NULL")
    c.ok(cur.fetchone()[0] == 0, "transactions without Plaid's detailed category")
    for b in get_annual_bills(cur, today):
        c.ok(b['set_aside'] >= 0, f"negative set-aside for {b['label']}")


# ---------------------------------------------------------------- rule checks

def rule_checks(c):
    from queries import month_periods, display_name, is_money_movement, nice_category, event_for
    from generate_report import judge, about, dollars
    from generate_narrative import check_opener

    # Month boundaries, including the year change.
    c.ok(month_periods(date(2027, 1, 5)) == (date(2026, 12, 1), date(2027, 1, 1),
                                             date(2026, 11, 1), date(2026, 12, 1)), "January periods")
    c.ok(month_periods(date(2026, 3, 1)) == (date(2026, 2, 1), date(2026, 3, 1),
                                             date(2026, 1, 1), date(2026, 2, 1)), "March 1 periods")

    # Verdicts: one rule, did we spend less than we earned (yearly bills spread
    # out as the set-aside). Savings going up or down does not change it.
    def cf(net, saved, bills=0.0):
        return {'net': net, 'saved': saved, 'annual_bills': bills}
    c.ok(judge(cf(2000, 500), 1200)[0] == 'within', "within means, savings up")
    c.ok(judge(cf(2000, -500), 1200)[0] == 'within', "within means even when savings dipped")
    c.ok(judge(cf(500, 500), 1200)[0] == 'over', "over when below the set-aside, even if savings grew")
    c.ok(judge(cf(-900, -2000), 1200)[0] == 'over', "over")
    c.ok(judge(cf(-3500, -5000, bills=4200), 1200)[0] == 'over', "tax month still over when short")
    c.ok(judge(cf(-3000, -5000, bills=4200), 1200)[0] == 'within', "tax month exactly even is within")
    c.ok(judge(cf(-1000, -5000, bills=4200), 1200)[0] == 'within', "tax month fine when covered")

    # Opener checks reject what the 8B model got wrong before.
    m = 'October'
    c.ok(not check_opener("You spent more in October than in September.", 'up', m), "good opener accepted")
    for bad, direction in [("You spent less in October.", 'up'),
                           ("October was your highest month ever.", 'up'),
                           ("In October you spent $1,234.56, more than before.", 'up'),
                           ("Your spending rose in October; consider a budget.", 'up'),
                           ("Spending went up in October. Here is the summary.", 'up'),
                           ("Spending increased in October.", 'down')]:
        c.ok(bool(check_opener(bad, direction, m)), f"bad opener accepted: {bad!r}")

    # Labels and classification, built from the active household settings so
    # no real payee names live in this file.
    from queries import (KNOWN_LARGE_BILLS, SAVINGS_PATTERNS, TRACKED_CARD_PAYMENT_PATTERN,
                         FIXED_BILLS, LINKED_EVENTS)

    def sample(pattern):
        return 'ACH DEBIT ' + pattern.strip('%').replace('%', ' ') + ' WEB X @@ 1'
    for label, pattern, _, _ in KNOWN_LARGE_BILLS:
        c.ok(display_name(sample(pattern)) == label, f"annual bill label: {label}")
    for pattern in SAVINGS_PATTERNS:
        c.ok(is_money_movement(sample(pattern), 'LOAN_PAYMENTS'), f"savings is movement: {pattern}")
    c.ok(is_money_movement(sample(TRACKED_CARD_PAYMENT_PATTERN), 'LOAN_PAYMENTS'), "tracked card payment is movement")
    for label, pattern in FIXED_BILLS:
        c.ok(not is_money_movement(sample(pattern), 'TRANSFER_OUT'), f"fixed bill is spend: {label}")
    c.ok(not is_money_movement('SOME STORE CARD PAYMENT', 'LOAN_PAYMENTS'), "untracked card payment is spend")
    c.ok(nice_category('GENERAL_SERVICES') == 'General services', "Plaid category label")
    c.ok(nice_category('Kids & daycare') == 'Kids & daycare', "household label untouched")
    for ev in LINKED_EVENTS:
        pattern, first, last, direction = ev['rules'][-1]
        name, amount = pattern.strip('%'), (100.0 if direction == 'in' else -100.0)
        hit = event_for(first, name, amount)
        c.ok(hit is not None and hit['short'] == ev['short'], f"event matched: {ev['short']}")
        c.ok(event_for(last + timedelta(days=400), name, amount) is None, f"event outside dates: {ev['short']}")

    # Schedule: Monday 07:00 weekly, 4th 07:00 monthly, catch up after missing.
    from datetime import datetime as dt
    import scheduler as sch
    c.ok(sch.last_weekly_time(dt(2026, 11, 9, 6, 59)) == dt(2026, 11, 2, 7), "Monday before 7 uses last week")
    c.ok(sch.last_weekly_time(dt(2026, 11, 9, 7, 0)) == dt(2026, 11, 9, 7), "Monday 7:00 is due")
    c.ok(sch.last_weekly_time(dt(2026, 11, 12, 15, 0)) == dt(2026, 11, 9, 7), "Thursday catches up Monday")
    c.ok(sch.last_monthly_time(dt(2026, 12, 4, 6, 0)) == dt(2026, 11, 4, 7), "4th before 7 uses last month")
    c.ok(sch.last_monthly_time(dt(2027, 1, 2, 9, 0)) == dt(2026, 12, 4, 7), "January 2nd uses December 4th")
    st = {'weekly_ok': '2026-11-09T07:05:00', 'monthly_ok': '2026-11-04T07:05:00'}
    c.ok(sch.due_job(dt(2026, 11, 10, 9, 0), st) is None, "nothing due mid-week after a run")
    c.ok(sch.due_job(dt(2026, 11, 16, 8, 0), st) == 'weekly', "next Monday is due")
    c.ok(sch.due_job(dt(2026, 12, 4, 7, 30), st) == 'monthly', "the 4th is due")
    c.ok(sch.due_job(dt(2026, 12, 6, 20, 0), st) == 'monthly', "a missed 4th runs when the Mac is back")
    c.ok(sch.in_backoff(dt(2026, 12, 4, 9, 0), {'last_failure': '2026-12-04T07:30:00'}), "waits after a failure")
    c.ok(not sch.in_backoff(dt(2026, 12, 4, 11, 0), {'last_failure': '2026-12-04T07:30:00'}), "retries later")
    card = {'name': 'Test card', 'closed_email_day': 12}
    cst = {'card_test_card_weekly_ok': '2026-11-09T07:05:00', 'card_test_card_closed_ok': '2026-10-12T07:05:00'}
    c.ok(sch.card_due(dt(2026, 11, 10, 9, 0), cst, card) is None, "card: nothing due mid-week")
    c.ok(sch.card_due(dt(2026, 11, 12, 7, 30), cst, card) == 'closed', "card: closed email on the 12th")
    c.ok(sch.card_due(dt(2026, 11, 14, 20, 0), cst, card) == 'closed', "card: missed 12th catches up")
    cst2 = dict(cst, card_test_card_closed_ok='2026-11-12T07:05:00')
    c.ok(sch.card_due(dt(2026, 11, 16, 7, 30), cst2, card) == 'weekly', "card: Monday weekly email")

    # Card billing cycles: start day inclusive, next start exclusive.
    from card_report import cycle_bounds, previous_cycle
    c.ok(cycle_bounds(date(2026, 11, 8), 11) == (date(2026, 10, 11), date(2026, 11, 11)), "cycle before the 11th")
    c.ok(cycle_bounds(date(2026, 11, 11), 11) == (date(2026, 11, 11), date(2026, 12, 11)), "cycle on the 11th")
    c.ok(cycle_bounds(date(2026, 12, 31), 11) == (date(2026, 12, 11), date(2027, 1, 11)), "cycle across new year")
    c.ok(cycle_bounds(date(2027, 1, 5), 11) == (date(2026, 12, 11), date(2027, 1, 11)), "January before the 11th")
    c.ok(cycle_bounds(date(2027, 2, 28), 31) == (date(2027, 2, 28), date(2027, 3, 31)), "31st clamps in February")
    c.ok(previous_cycle(date(2026, 11, 11), 11) == (date(2026, 10, 11), date(2026, 11, 11)), "previous cycle")

    # Formatting.
    c.ok(dollars(-1234.56) == '−$1,235', "negative dollars")
    c.ok(dollars(0.0, sign=True) == '$0', "zero has no sign")
    c.ok(about(7749) == 'about $7,700', "rounding to $100")


def run(today=None, verbose=True):
    """Returns the list of failures (empty means everything passed)."""
    from queries import get_connection
    today = today or date.today()
    c = Checks()
    rule_checks(c)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            data_checks(c, cur, today)
    finally:
        conn.close()
    if verbose:
        if c.failures:
            print(f"CHECKS FAILED: {len(c.failures)} of {c.count}")
            for f in c.failures:
                print(f"  - {f}")
        else:
            print(f"All {c.count} checks passed.")
    return c.failures


if __name__ == '__main__':
    sys.exit(1 if run() else 0)
