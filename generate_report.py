"""One self-contained HTML report: narrative + charts (embedded as base64).

    python generate_report.py   # writes output/report.html

Everything shown comes from generate_narrative.build_narrative() and
generate_charts.build_charts(), for the same month.
"""
import base64
import html
import os
from datetime import date, datetime, timedelta

from queries import (get_connection, get_monthly_overview, get_annual_bills, get_linked_events,
                     event_for, get_forward_estimate, get_spend_by_household, KNOWN_LARGE_BILLS,
                     SAVINGS_LABEL)
from household import CONFIG as _HC

BILL_LABELS = {label for label, *_ in KNOWN_LARGE_BILLS}
BILL_MONTHS = {label: months for label, _, _, months in KNOWN_LARGE_BILLS}


def month_names(months):
    names = [date(2000, m, 1).strftime('%B') for m in months]
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
from generate_charts import build_charts
from generate_narrative import build_narrative, money

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'output')
REPORT_PATH = os.path.join(OUTPUT_DIR, 'report.html')

CHART_ORDER = [
    ('daily_spend.png', 'Daily spend'),
    ('category_spend.png', 'Spend by category'),
    ('account_spend.png', 'Spend by account or card'),
]

CSS = """
:root { --bg:#f6f6f4; --card:#ffffff; --text:#1d1d1b; --muted:#6b6b66; --line:#e3e3de;
        --up:#a33a2b; --down:#2e6b3f; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161615; --card:#21211f; --text:#ecece8; --muted:#a3a39c; --line:#33332f;
          --up:#e98a7a; --down:#8fcf9f; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 32px 16px 48px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; }
.meta { color: var(--muted); font-size: .875rem; margin-bottom: 24px; }
.tiles { display:grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap:12px; margin-bottom:24px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
.tile .label { color:var(--muted); font-size:.8rem; }
.tile .value { font-size:1.35rem; font-weight:600; font-variant-numeric: tabular-nums; }
.up { color: var(--up); } .down { color: var(--down); }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:20px 22px; margin-bottom:24px; }
.opener { font-size:1.1rem; font-weight:500; }
.card p { margin: 0 0 14px; } .card p:last-child { margin-bottom: 0; }
h2 { font-size: 1.05rem; margin: 0 0 10px; }
table { width:100%; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: .95rem; }
td, th { padding: 6px 0; border-bottom: 1px solid var(--line); text-align: left; }
th { color: var(--muted); font-weight: 500; font-size: .8rem; }
td.num, th.num { text-align: right; padding-left: 12px; white-space: nowrap; }
tr.sub td:first-child { padding-left: 18px; color: var(--muted); }
tr.total td { font-weight: 600; border-bottom: 0; }
.plain { margin-bottom: 32px; }
.plain p { margin: 0 0 10px; font-size: 1.05rem; }
.verdict { display:flex; gap:14px; align-items:flex-start; border-radius:12px; padding:18px 20px; margin-bottom:16px; }
.v-sym { font-size: 1.8rem; line-height: 1; font-weight: 700; }
.v-title { font-size: 1.35rem; font-weight: 700; margin-bottom: 4px; }
.v-text { font-size: 1.05rem; }
.chips { display:grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap:8px; margin-bottom:18px; }
.chip { border-radius:10px; padding:10px 12px; font-size:.85rem; line-height:1.4; }
.chip-m { font-weight:700; font-size:.95rem; }
.chip-why { margin-top:4px; font-style: italic; } .chip-v { font-weight:600; margin-bottom:2px; }
.v-good { background:#e3f2e7; color:#1d4f2c; } .v-ok { background:#e6eef8; color:#1f3f66; }
.v-watch { background:#fbf0d9; color:#6b4a0c; } .v-bad { background:#f8e1de; color:#7a261b; }
@media (prefers-color-scheme: dark) {
  .v-good { background:#1f3a28; color:#bfe6c9; } .v-ok { background:#1d2c40; color:#c4d8f2; }
  .v-watch { background:#3d3015; color:#f2dcaa; } .v-bad { background:#45221d; color:#f3c3bb; }
}
p.how { color: var(--muted); font-size: .9rem !important; }
h2.section { font-size: 1.2rem; margin: 8px 0 12px; padding-top: 16px; border-top: 1px solid var(--line); }
.overview .headline p { margin: 0 0 8px; }
.overview .note { color: var(--muted); font-size: .85rem; margin-top: 12px; }
.overview figure { margin: 16px 0 0; }
.scroll { overflow-x: auto; }
.attention { border-color: #c9962c; } .attention.ok { border-color: var(--line); color: var(--muted); }
.attention ul { margin: 0; padding-left: 18px; } .attention li { margin-bottom: 8px; }
table.months td:first-child { white-space: nowrap; padding-right: 12px; }
.overview table { font-size: .9rem; }
@media (max-width: 480px) { .overview .wide { display: none; } }
.overview h3 { font-size: .95rem; margin: 16px 0 6px; }
ul.big { margin: 0; padding-left: 18px; font-size: .9rem; } ul.big li { margin-bottom: 4px; }
ul.big em { color: var(--muted); font-style: normal; }
.muted { color: var(--muted); font-size: .8rem; }
.overview th { white-space: normal; }
figure { margin:0 0 24px; background:#fff; border:1px solid var(--line); border-radius:10px; padding:12px; }
figure img { width:100%; height:auto; display:block; }
figcaption { color:#6b6b66; font-size:.85rem; margin-top:6px; }
footer { color:var(--muted); font-size:.8rem; }
"""


def tile(label, value, cls=''):
    return (f'<div class="tile"><div class="label">{html.escape(label)}</div>'
            f'<div class="value {cls}">{html.escape(value)}</div></div>')


def out(x):
    """Money leaving, shown as a negative: -$1,234.00 (zero stays $0.00)."""
    return ('−' if x > 0.005 else '') + money(abs(x))


def row(label, *values, cls=''):
    cells = ''.join(f'<td class="num">{html.escape(v)}</td>' for v in values)
    return f'<tr class="{cls}"><td>{html.escape(label)}</td>{cells}</tr>'


def cash_flow_table(d):
    cf = d['cash_flow']
    rows = [row('Paychecks', money(cf['paychecks'])),
            row('Other income', money(cf['other_income'])),
            row('From other people (Venmo, Zelle, deposits)', money(cf['from_others']))]
    rows += [row(f"{n} ({dt:%b} {dt.day})", money(a), cls='sub') for dt, n, a in cf['large_in_items']]
    if cf['large_in']:
        rows.insert(3, row('Large one-time amounts', money(cf['large_in'])))
    rows.append(row('Money in', money(cf['money_in']), cls='total'))
    rows += [row('Regular spending', out(cf['regular_spend'])),
             row('Large one-time purchases', out(cf['one_off_spend'])),
             row('Paid to other people (checks, Venmo, Zelle)', out(cf['to_others']))]
    rows.append(row('Money out', out(cf['money_out']), cls='total'))
    rows.append(row('Net (in minus out)', ('−' if cf['net'] < 0 else '') + money(abs(cf['net'])), cls='total'))
    rows.append(row(f'Saved, net ({SAVINGS_LABEL}; not in money out)', ('−' if cf['saved'] < 0 else '') + money(abs(cf['saved']))))
    return (f'<div class="card"><h2>Cash flow, {html.escape(d["cur_month"])} (checking accounts)</h2>'
            f'<table>{"".join(rows)}</table></div>')


def household_table(by_month):
    """by_month: [(month_start, {category: total})], oldest first."""
    if not by_month:
        return ''
    totals = {}
    for _, cats in by_month:
        for c, a in cats.items():
            totals[c] = totals.get(c, 0.0) + a
    names = sorted(totals, key=lambda c: (c == 'Other', -totals[c]))
    n = len(by_month)
    head = ('<tr><th></th>' + ''.join(f'<th class="num">{m:%b}</th>' for m, _ in by_month)
            + '<th class="num">Average</th></tr>')
    rows = []
    for c in names:
        cells = ''.join(f'<td class="num">{dollars(cats.get(c, 0.0))}</td>' for _, cats in by_month)
        rows.append(f'<tr><td>{html.escape(c)}</td>{cells}<td class="num">{dollars(totals[c] / n)}</td></tr>')
    month_totals = [sum(cats.values()) for _, cats in by_month]
    rows.append('<tr class="total"><td>Total</td>'
                + ''.join(f'<td class="num">{dollars(t)}</td>' for t in month_totals)
                + f'<td class="num">{dollars(sum(month_totals) / n)}</td></tr>')
    return (f'<div class="card"><h2>Where the money went, regular spending by month</h2>'
            f'<div class="scroll"><table class="months">{head}{"".join(rows)}</table></div>'
            f'<p class="note">Sorted by total across these months. Categories come from merchant rules first '
            f'(in household.toml), then Plaid\'s detailed category. Rules live in '
            f'<code>merchant_rules in household.toml</code>. Annual bills and other one-time items of $1,500 or more '
            f'are not included.</p></div>')


def class_table(d):
    prev = {cls: (amt, dict(items)) for cls, amt, items in (d['classes_prev'] or [])}
    head = ('<tr><th></th><th class="num">' + html.escape(d['cur_month']) + '</th>'
            + (f'<th class="num">{html.escape(d["prev_month"])}</th>' if prev else '') + '</tr>')
    rows = []
    for cls, amt, items in d['classes']:
        p_amt, p_items = prev.get(cls, (None, {}))
        rows.append(row(cls, money(amt), *([money(p_amt)] if prev else []), cls='total'))
        for label, a in items:
            rows.append(row(label, money(a), *([money(p_items.get(label, 0.0))] if prev else []), cls='sub'))
    return (f'<div class="card"><h2>Fixed vs variable, regular spending</h2>'
            f'<table>{head}{"".join(rows)}</table></div>')


def dollars(x, sign=False):
    """Whole dollars for the overview: $1,565 or, with sign, +$1,565 / −$1,565."""
    pre = ('+' if x >= 0.5 else '−' if x <= -0.5 else '') if sign else ('−' if x <= -0.5 else '')
    return f"{pre}${abs(x):,.0f}"


def savings_text(v):
    if v >= 0.5:
        return f"{dollars(v)} into savings"
    if v <= -0.5:
        return f"{dollars(-v)} out of savings"
    return "no change"


def overview_headline(rows, bills_info=()):
    """Plain sentences, all computed here, answering: are we adding to savings
    or pulling from it?"""
    n = len(rows)
    first, last = f"{rows[0][0]:%B}", f"{rows[-1][0]:%B}"
    total_saved = sum(cf['saved'] for _, cf in rows)
    up = [m for m, cf in rows if cf['saved'] >= 0.5]
    down = [(m, cf['saved']) for m, cf in rows if cf['saved'] <= -0.5]
    covered = sum(1 for _, cf in rows if cf['net'] >= 0)
    everyday_pos = sum(1 for _, cf in rows if cf['everyday_net'] >= 0)
    everyday_avg = sum(cf['everyday_net'] for _, cf in rows) / n

    lines = []
    last_m, last_cf = rows[-1]
    lines.append(f"<strong>{last_m:%B}: {html.escape(savings_text(last_cf['saved']))}</strong>, "
                 f"and {dollars(abs(last_cf['net']))} "
                 + ("left over after spending." if last_cf['net'] >= 0 else "more spent than came in."))
    span = f"From {first} to {last}" if n > 1 else f"In {first}"
    if abs(total_saved) < 0.5:
        lines.append(f"{span}, savings deposits and withdrawals evened out.")
    else:
        lines.append(f"{span}, {dollars(abs(total_saved))} more "
                     + ("went into savings than came out." if total_saved > 0 else "came out of savings than went in."))
    if n > 1:
        s = f"Savings went up in {len(up)} of {n} months"
        if down:
            s += "; money came out in " + ", ".join(f"{m:%B} ({dollars(-v)})" for m, v in down)
        lines.append(s + ".")
        for m, _ in down:
            cf = dict(rows)[m]
            bills = [(n, a) for _, n, a in cf['one_off_items'] if n in BILL_LABELS]
            if bills:
                lines.append(f"{m:%B} included " + "; ".join(
                    f"{n.lower()} ({dollars(a)}, paid every {month_names(BILL_MONTHS[n])})" for n, a in bills) + ".")
        lines.append(f"Money in covered money out in {covered} of {n} months. Leaving out annual bills and "
                     f"other large one-time items, {everyday_pos} of {n} months had money left over, "
                     f"{dollars(everyday_avg)} a month on average (at past pay, before setting money aside "
                     f"for yearly bills; see the going-forward estimate below).")
    return ''.join(f"<p>{l}</p>" for l in lines)


def overview_section(rows, chart_png, bills_info=(), events=None, forecast=None):
    if not rows:
        return ''
    head = ('<tr><th>Month</th><th class="num">Savings</th><th class="num">Left over</th>'
            '<th class="num wide">Money in</th><th class="num wide">Money out</th></tr>')
    body = []
    for m, cf in rows:
        cls = 'down' if cf['saved'] >= 0.5 else 'up' if cf['saved'] <= -0.5 else ''
        body.append(f'<tr><td>{m:%b %Y}</td>'
                    f'<td class="num {cls}">{dollars(cf["saved"], sign=True)}</td>'
                    f'<td class="num">{dollars(cf["net"], sign=True)}</td>'
                    f'<td class="num wide">{dollars(cf["money_in"])}</td>'
                    f'<td class="num wide">{dollars(cf["money_out"])}</td></tr>')
    tot_saved = sum(cf['saved'] for _, cf in rows)
    tot_net = sum(cf['net'] for _, cf in rows)
    body.append(f'<tr class="total"><td>Total</td>'
                f'<td class="num">{dollars(tot_saved, sign=True)}</td>'
                f'<td class="num">{dollars(tot_net, sign=True)}</td>'
                f'<td class="num wide">{dollars(sum(cf["money_in"] for _, cf in rows))}</td>'
                f'<td class="num wide">{dollars(sum(cf["money_out"] for _, cf in rows))}</td></tr>')
    big = []
    for m, cf in rows:
        items = ([(dt, n, a) for dt, n, a in cf['large_in_items']]
                 + [(dt, n, -a) for dt, n, a in cf['one_off_items']]
                 + [(dt, n, -a) for dt, n, a in cf['large_out_items']])
        month_events = (events or {}).get(m, [])
        items = sorted(i for i in items if event_for(i[0], i[1], i[2]) is None)
        parts = [f"{html.escape(label)}: {dollars(out_)} out, {dollars(in_)} back, "
                 f"<strong>{dollars(net, sign=True)} net</strong>"
                 for _, label, out_, in_, net in month_events]
        parts += [f"{html.escape(n)} {dollars(a, sign=True)}"
                  + (" <em>(necessary annual bill)</em>" if n in BILL_LABELS else "")
                  for _, n, a in items]
        if parts:
            big.append(f"<li><strong>{m:%b}:</strong> " + "; ".join(parts) + "</li>")
    big_html = (f'<h3>Annual bills and other large one-time items</h3><ul class="big">{"".join(big)}</ul>'
                if big else '')
    big_html += bills_table(bills_info) + forecast_table(forecast)
    fig = (f'<figure><img alt="Left over and savings by month" src="data:image/png;base64,'
           f'{base64.b64encode(chart_png).decode()}"></figure>') if chart_png else ''
    return f"""<section class="card overview">
    <h2>Are we adding to savings or pulling from it?</h2>
    <div class="headline">{overview_headline(rows, bills_info)}</div>
    <div class="scroll"><table>{head}{''.join(body)}</table></div>
    {big_html}
    {fig}
    <p class="note">On a phone, money in and money out are hidden; turn the phone sideways to see them.
    <strong>Left over</strong> is money in minus money out on the checking accounts.
    <strong>Savings</strong> is money into savings ({html.escape(SAVINGS_LABEL)}) minus withdrawals
    (+ means money went in). The two can differ in a month because card bills are paid from
    checking the month after the purchases, and the checking balance absorbs the difference.
    Only money moving in and out of savings is visible here, not its balance.</p>
  </section>"""


def forecast_table(f):
    if not f:
        return ''
    rows = [row('Expected take-home pay (current rates)', dollars(f['pay'])),
            row(f"Fixed bills (at {f['fixed_month']:%B}'s level)", dollars(-f['fixed'])),
            row(f"Variable spending (average of {f['n']} months)", dollars(-f['variable'])),
            row('Small payments to other people (average)', dollars(-f['to_others'])),
            row('Set aside for yearly bills', dollars(-f['set_aside'])),
            row('Left for savings in a normal month', ('−' if f['left'] < 0 else '') + dollars(abs(f['left'])),
                cls='total')]
    return ('<h3>A normal month going forward</h3><table>' + ''.join(rows) + '</table>'
            '<p class="note">Pay comes from <code>[[pay.current]]</code> in household.toml (set by hand; update '
            'it when pay changes). Spending comes from the months above. One-time money (payouts, sales, '
            'paybacks) is not counted.</p>')


def bills_table(bills_info):
    if not bills_info:
        return ''
    rows = []
    for b in bills_info:
        last = (f"{dollars(b['last_amount'])} on {b['last_date']:%b} {b['last_date'].day}, {b['last_date']:%Y}"
                if b['last_date'] else 'not seen yet')
        nxt = f"{b['next_due']:%b %Y}" + (" — overdue, no payment found" if b['overdue'] else "")
        rows.append(f"<tr><td>{html.escape(b['label'])}<br><span class=\"muted\">every "
                    f"{month_names(b['months'])}</span></td><td class=\"num wide\">{last}</td>"
                    f"<td class=\"num\">{html.escape(nxt)}</td><td class=\"num\">{dollars(b['set_aside'])}</td></tr>")
    total = sum(b['set_aside'] for b in bills_info)
    rows.append(f'<tr class="total"><td>Total</td><td class="wide"></td><td></td>'
                f'<td class="num">{dollars(total)}</td></tr>')
    return ('<h3>Planned annual bills</h3><div class="scroll"><table>'
            '<tr><th>Bill</th><th class="num wide">Last paid</th><th class="num">Next due</th>'
            '<th class="num">Set aside per month</th></tr>' + ''.join(rows) + '</table></div>'
            '<p class="note">Set aside per month is the last payment times payments per year, divided by 12. '
            'It assumes the next payment is about the same as the last one.</p>')


# ---------------------------------------------------------------- plain summary
# Written for a reader who is not a numbers person: one verdict, a colored
# strip of months, three short sentences, round numbers. Every word is chosen
# by code from the rules below; the model is not involved.

VERDICTS = {
    # key: (title, css class, symbol)
    'within': ('Spent less than we earned', 'v-good', '✓'),
    'over': ('Spent more than we earned', 'v-watch', '!'),
}
HOW_JUDGED = ("How each month is judged: did more money come in than went out? Big yearly bills like "
              "property taxes count as {set_aside} every month instead of all at once, so the month we pay "
              "them isn't counted against us.")


def about(x):
    """Round to the nearest $100 for the plain summary: 'about $7,700'."""
    return f"about ${round(abs(x), -2):,.0f}"


def plain_bill(label):
    """'Home and auto insurance (full year)' -> 'home and auto insurance'."""
    return label.split(' (')[0].lower()


def judge(cf, set_aside):
    """One rule: did we spend less than we earned? Money in minus money out,
    counting yearly bills as the monthly set-aside instead of the month they
    were paid. Whether savings went up is shown as a fact, not judged."""
    fair = cf['net'] + cf['annual_bills'] - set_aside
    within = fair >= 0
    return ('within' if within else 'over'), cf['saved'] >= 0, within, fair


def verdict_sentence(m, cf, key):
    bills = [plain_bill(n) for _, n, _ in cf['annual_bill_items']]
    if len(bills) == 1:
        bill_note = f" {m:%B} had the yearly {bills[0]} bill, which the money set aside each month covers."
    elif bills:
        bill_note = (f" {m:%B} had {len(bills)} yearly bills ({' and '.join(bills)}), which the money set "
                     f"aside each month covers.")
    else:
        bill_note = ""
    first = "More money came in than went out." if key == 'within' else "More money went out than came in."
    if cf['saved'] >= 50:
        savings = f" Savings went up {about(cf['saved'])}."
    elif cf['saved'] <= -50:
        savings = f" Savings went down {about(cf['saved'])}."
    else:
        savings = " Savings stayed about the same."
    return first + savings + bill_note


def chip_reason(m, cf, key, events):
    """For months that overspent or where savings went down: the big things that
    happened, in a few words."""
    if key == 'within' and cf['saved'] >= 0:
        return ''
    reasons = [short for short, *_ in (events or {}).get(m, [])]
    reasons += [plain_bill(n) for _, n, _ in cf['annual_bill_items']]
    return (", ".join(reasons[:3])).capitalize() if reasons else ''


# Bills the reader already knows about; the 'where it went' line skips them.
BASICS = tuple(_HC.get('report', {}).get('basics', ()))
BASICS_PHRASE = _HC.get('report', {}).get('basics_phrase', 'the regular bills')
PAY_PHRASE = _HC.get('report', {}).get('pay_phrase', 'At current pay')


def summary_content(rows, bills_info, events=None, forecast=None, categories=None):
    """Everything the 'How are we doing?' section says, as data, so the web
    page and the email text say exactly the same thing."""
    if not rows:
        return None
    set_aside = sum(b['set_aside'] for b in bills_info)
    judged = [(m, cf) + judge(cf, set_aside) for m, cf in rows]
    m, cf, key, _, _, _ = judged[-1]
    months = []
    for mm, c, k, up, within, _ in judged:
        months.append({'month': mm, 'key': k, 'saved': c['saved'], 'up': up, 'within': within,
                       'why': chip_reason(mm, c, k, events)})

    lines = []
    normal = forecast['left'] if forecast else None
    if normal is not None and normal >= 0:
        lines.append(f"{PAY_PHRASE}, a normal month should leave {about(normal)} after all the "
                     f"regular bills and putting money aside for the yearly ones. That is what can go to savings.")
    elif normal is not None:
        lines.append(f"{PAY_PHRASE}, regular spending plus putting money aside for the yearly bills "
                     f"would be {about(normal)} more than we take home in a normal month.")
    total_saved = sum(c['saved'] for _, c in rows)
    first = f"{rows[0][0]:%B}"
    if abs(total_saved) < 1000:
        lines.append(f"Since {first}, savings is about where it started"
                     + ("; big one-time costs used up what the normal months added."
                        if sum(c['everyday_net'] for _, c in rows) > 0 else "."))
    elif total_saved > 0:
        lines.append(f"Since {first}, savings has grown by {about(total_saved)}.")
    else:
        lines.append(f"Since {first}, savings has gone down by {about(total_saved)}.")
    top = [(c, a) for c, a in (categories or []) if c not in BASICS and c != 'Other'][:3]
    if top:
        parts = [f"{c} ({about(a)})" for c, a in top]
        lines.append(f"Besides {BASICS_PHRASE}, the most went to "
                     + (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0])
                     + f" in {rows[-1][0]:%B}.")
    upcoming = sorted((b for b in bills_info if b['last_amount']), key=lambda b: b['next_due'])
    if upcoming:
        b = upcoming[0]
        when = "overdue (no payment found yet)" if b['overdue'] else f"in {b['next_due']:%B}"
        lines.append(f"Next big bill: {plain_bill(b['label'])} {when}, {about(b['last_amount'])}. "
                     f"Putting {about(set_aside)} a month aside covers all the yearly bills.")
    return {'month': m, 'key': key, 'verdict': verdict_sentence(m, cf, key), 'months': months,
            'lines': lines, 'set_aside': set_aside}


def saved_text(x):
    return f"Savings {'up' if x >= 0 else 'down'} ${round(abs(x), -2):,.0f}"


def plain_summary(rows, bills_info, events=None, forecast=None, categories=None):
    sc = summary_content(rows, bills_info, events, forecast, categories)
    if not sc:
        return ''
    title, cls, sym = VERDICTS[sc['key']]
    chips = []
    for mo in sc['months']:
        t, kc, ks = VERDICTS[mo['key']]
        chips.append(
            f'<div class="chip {kc}"><div class="chip-m">{mo["month"]:%b}</div>'
            f'<div class="chip-v">{ks} {html.escape(t)}</div>'
            f'<div class="chip-l">{saved_text(mo["saved"])}</div>'
            + (f'<div class="chip-why">{html.escape(mo["why"])}</div>' if mo['why'] else '')
            + '</div>')
    return f"""<section class="plain">
    <div class="verdict {cls}"><div class="v-sym">{sym}</div><div>
      <div class="v-title">{sc['month']:%B}: {html.escape(title)}</div>
      <div class="v-text">{html.escape(sc['verdict'])}</div></div></div>
    <div class="chips">{''.join(chips)}</div>
    {''.join(f'<p>{html.escape(l)}</p>' for l in sc['lines'])}
    <p class="how">{html.escape(HOW_JUDGED.format(set_aside=about(sc['set_aside'])))}</p>
  </section>"""


def summary_text(sc):
    """The same content as plain text laid out for an email: headings, blank
    lines between sections, one line per month."""
    if not sc:
        return ''
    import re
    title = VERDICTS[sc['key']][0]
    sentences = [x for x in re.split(r'(?<=\.) ', sc['verdict']) if x]
    out = [f"{sc['month']:%B}: {title.upper()}", ''] + sentences + ['', 'MONTH BY MONTH']
    for mo in sc['months']:
        t = VERDICTS[mo['key']][0]
        line = f"  • {mo['month']:%B} — {VERDICTS[mo['key']][2]} {t}. {saved_text(mo['saved'])}."
        if mo['why']:
            line += f" ({mo['why']})"
        out.append(line)
    out += ['', 'THE BIGGER PICTURE'] + [f"  • {l}" for l in sc['lines']]
    out += ['', HOW_JUDGED.format(set_aside=about(sc['set_aside']))]
    return '\n'.join(out)


def render(narrative, charts, generated_at, overview_rows=(), bills_info=(), events=None, forecast=None,
           household=None, attention=None):
    d = narrative['data']
    tiles = [tile(f"Regular spend, {d['cur_name']}", money(d['total']))]
    if d['direction'] != 'none':
        tiles.append(tile(f"{d['prev_name']}", money(d['total_prev'])))
        cls = {'up': 'up', 'down': 'down'}.get(d['direction'], '')
        tiles.append(tile("Change", f"{d['pct']:+.1f}%", cls))
    net = d['cash_flow']['net']
    tiles.append(tile(f"Net cash flow, {d['cur_month']}", ('+' if net >= 0 else '−') + money(abs(net)),
                      'down' if net >= 0 else 'up'))
    if d['show_mtd']:
        tiles.append(tile(f"Month to date (through {d['mtd_through']:%b} {d['mtd_through'].day})", money(d['mtd'])))

    paragraphs = ''.join(f'<p>{html.escape(p)}</p>' for p in narrative['report_paragraphs'])
    figures = ''.join(
        f'<figure><img alt="{html.escape(caption)}" src="data:image/png;base64,'
        f'{base64.b64encode(charts[name]).decode()}"><figcaption>{html.escape(caption)}</figcaption></figure>'
        for name, caption in CHART_ORDER if name in charts)

    source = narrative['opener_source']
    opener_note = ("Opening sentence written by the local model and checked by code."
                   if source == 'model' else f"Opening sentence is a fixed sentence ({html.escape(source)}).")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>How are we doing? — {html.escape(d['cur_name'])}</title>
<style>{CSS}</style>
</head>
<body>
<main>
  <h1>How are we doing?</h1>
  <div class="meta">Through {html.escape(d['cur_name'])} · generated {generated_at:%b} {generated_at.day}, {generated_at:%Y at %H:%M}</div>
  {plain_summary(list(overview_rows), list(bills_info), events, forecast, d.get('categories'))}
  <h2 class="section">Details</h2>
  {attention_box(attention)}
  {overview_section(list(overview_rows), charts.get('savings_by_month.png'), list(bills_info), events, forecast)}
  <h2 class="section">{html.escape(d['cur_name'])} in detail</h2>
  <div class="tiles">{''.join(tiles)}</div>
  <div class="card">
    <p class="opener">{html.escape(narrative['opener'])}</p>
    {paragraphs}
  </div>
  {cash_flow_table(d)}
  {household_table(household or [])}
  {class_table(d)}
  {figures}
  <footer>All numbers and sentences after the first are computed by code from the database. {opener_note}
  Regular spend excludes transfers, savings, tracked card payments, annual bills and other one-time items of $1,500 or more.</footer>
</main>
</body>
</html>
"""


def attention_box(found):
    from queries import REVIEWED_THROUGH
    if found is None:
        return ''
    if not found:
        return (f'<div class="card attention ok"><strong>Needs attention:</strong> nothing. The settings '
                f'explain everything through today (reviewed through {REVIEWED_THROUGH:%b} '
                f'{REVIEWED_THROUGH.day}).</div>')
    items = ''.join(f'<li>{html.escape(what)}<br><span class="muted">{html.escape(todo)}</span></li>'
                    for what, todo in found)
    return (f'<div class="card attention"><h2>Needs attention ({len(found)})</h2><ul>{items}</ul>'
            f'<p class="note">After dealing with these, move <code>reviewed_through in household.toml</code> to '
            f'today so they stop showing.</p></div>')


def main():
    import sys
    import checks
    from attention import get_attention
    global REPORT_PATH
    # REPORT_DATE (YYYY-MM-DD) and --out PATH exist for the demo database; normal
    # runs use today and output/report.html.
    today = date.fromisoformat(os.environ['REPORT_DATE']) if os.getenv('REPORT_DATE') else date.today()
    if '--out' in sys.argv:
        REPORT_PATH = os.path.abspath(sys.argv[sys.argv.index('--out') + 1])
    failures = checks.run(today)
    if failures:
        print("Report NOT written because checks failed. Fix the problems above, then rerun.")
        sys.exit(1)
    narrative = build_narrative(today)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            charts = build_charts(cur, today)
            overview_rows, _ = get_monthly_overview(cur, today)
            bills_info = get_annual_bills(cur, today)
            forecast = get_forward_estimate(cur, overview_rows, sum(b['set_aside'] for b in bills_info))
            found = get_attention(cur, today)
            household = [(m, {c: a for c, a, _ in get_spend_by_household(
                              cur, m, (m + timedelta(days=32)).replace(day=1))})
                         for m, _ in overview_rows]
            events = {m: get_linked_events(cur, m, (m + timedelta(days=32)).replace(day=1))
                      for m, _ in overview_rows}
    finally:
        conn.close()

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    tmp = REPORT_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(render(narrative, charts, datetime.now(), overview_rows, bills_info, events, forecast, household,
                       found))
    os.replace(tmp, REPORT_PATH)  # never leave a half-written report
    with open(os.path.join(os.path.dirname(REPORT_PATH), 'summary.txt' if REPORT_PATH.endswith('report.html')
                           else os.path.basename(REPORT_PATH).replace('.html', '_summary.txt')),
              'w', encoding='utf-8') as f:
        f.write(summary_text(summary_content(list(overview_rows), list(bills_info), events, forecast,
                                             narrative['data'].get('categories'))) + '\n')
    print(f"Report written to {REPORT_PATH} ({os.path.getsize(REPORT_PATH) // 1024} KB)")
    print(f"Opener: {narrative['opener_source']}")
    print(f"Needs attention: {len(found)} item(s)" + (" (see the report)" if found else ""))


if __name__ == '__main__':
    main()
