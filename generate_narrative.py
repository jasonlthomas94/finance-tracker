"""Monthly spending narrative.

Code computes every number and writes every sentence that contains a number,
name, date or direction. The local model writes only a one-sentence opener from
the month names and a direction word, and that opener is rejected (then retried,
then replaced with a fixed sentence) if it contains numbers, superlatives,
advice, or the wrong direction.

    python generate_narrative.py           # print the narrative
    python generate_narrative.py --facts   # also print what the model was given
"""
import re
import sys
from datetime import date, timedelta

from queries import (
    get_connection, get_large_one_offs, get_spend_by_household,
    get_spend_by_account, get_top_merchants, get_total_spend, get_daily_spend,
    get_largest_regular_charge, is_money_movement, get_cash_flow, get_spend_by_class,
    display_name, is_known_bill, SAVINGS_LABEL,
    ONE_OFF_THRESHOLD, HISTORY_START, month_periods, month_label, nice_category,
)

MODEL = 'llama3.1:8b'
OPENER_ATTEMPTS = 3
FLAT_PCT = 5          # |change| under this is "about the same"
MOVER_MIN = 200       # category changes smaller than this are not mentioned
SPIKE_FACTOR = 2.5    # a day is a spike above this multiple of the daily average


def money(x):
    return f"${float(x):,.2f}"


def nice_date(d):
    return f"{d:%b} {d.day}"


def join_list(items):
    items = list(items)
    if len(items) <= 1:
        return ''.join(items)
    return ', '.join(items[:-1]) + ' and ' + items[-1]


def movement_label(name):
    from queries import SAVINGS_PATTERNS, TRACKED_CARD_PAYMENT_PATTERN
    from household import CONFIG
    n = (name or '').upper()
    if any(p.strip('%').upper() in n for p in SAVINGS_PATTERNS):
        return CONFIG['savings'].get('movement_label', 'transfers to savings')
    if TRACKED_CARD_PAYMENT_PATTERN.strip('%').upper() in n:
        return CONFIG['cards'].get('movement_label', 'credit card payments')
    return 'other transfers'


# ---------------------------------------------------------------- data

def compute(cur, today):
    cur_start, cur_end, prev_start, prev_end = month_periods(today)
    d = {
        'cur_name': month_label(cur_start), 'prev_name': month_label(prev_start),
        'cur_month': f"{cur_start:%B}", 'prev_month': f"{prev_start:%B}",
        'comparison': prev_start >= HISTORY_START,
        'total': float(get_total_spend(cur, cur_start, cur_end)),
        'total_prev': float(get_total_spend(cur, prev_start, prev_end)),
        'mtd': float(get_total_spend(cur, cur_end, today)),
        'mtd_through': today - timedelta(days=1),
        'show_mtd': today.day > 1,
    }
    d['pct'] = ((d['total'] - d['total_prev']) / d['total_prev'] * 100
                if d['comparison'] and d['total_prev'] > 0 else None)
    if d['pct'] is None:
        d['direction'] = 'none'
    elif abs(d['pct']) < FLAT_PCT:
        d['direction'] = 'flat'
    else:
        d['direction'] = 'up' if d['pct'] > 0 else 'down'

    cur_cat = [(c, a) for c, a, _ in get_spend_by_household(cur, cur_start, cur_end)]
    prev_cat = {c: a for c, a, _ in get_spend_by_household(cur, prev_start, prev_end)}
    d['categories'] = cur_cat
    d['categories_prev'] = prev_cat
    d['top_categories'] = cur_cat[:3]
    d['movers'] = []
    if d['comparison']:
        cur_map = dict(cur_cat)
        for c in set(cur_map) | set(prev_cat):
            now, before = cur_map.get(c, 0.0), prev_cat.get(c, 0.0)
            if abs(now - before) >= MOVER_MIN:
                d['movers'].append((c, now - before, before, now))
        d['movers'].sort(key=lambda m: abs(m[1]), reverse=True)
        d['movers'] = d['movers'][:4]

    by_account = [(n, float(a)) for n, a in get_spend_by_account(cur, cur_start, cur_end)]
    d['top_account'] = by_account[0] if by_account and by_account[0][1] > 0 else None
    d['idle_accounts'] = [n for n, a in by_account if a == 0]
    d['top_merchants'] = [(m, float(a), n) for m, a, n in get_top_merchants(cur, cur_start, cur_end)[:5]]

    # Spikes ignore every fixed expense in EXPENSE_CLASSES: they land on the
    # same days every month and are not "unusual".
    days = (cur_end - cur_start).days
    variable = get_daily_spend(cur, cur_start, cur_end, exclude_fixed=True)
    d['avg_daily'] = sum(float(a) for _, a in variable) / days
    d['spikes'] = []
    for day, amt in variable:
        if float(amt) > d['avg_daily'] * SPIKE_FACTOR:
            m, a = get_largest_regular_charge(cur, day, exclude_fixed=True)
            d['spikes'].append((day, float(amt), m, float(a)))

    one_offs = get_large_one_offs(cur, cur_start, cur_end)
    d['one_offs'] = [(dt, display_name(n), float(a), is_known_bill(n))
                     for n, a, dt, c in one_offs if not is_money_movement(n, c)]
    groups = {}
    for n, a, dt, c in one_offs:
        if is_money_movement(n, c):
            count, total = groups.get(movement_label(n), (0, 0.0))
            groups[movement_label(n)] = (count + 1, total + float(a))
    d['movement'] = sorted(groups.items(), key=lambda g: -g[1][1])

    d['cash_flow'] = get_cash_flow(cur, cur_start, cur_end)
    d['classes'] = get_spend_by_class(cur, cur_start, cur_end)
    d['classes_prev'] = get_spend_by_class(cur, prev_start, prev_end) if d['comparison'] else None
    return d


# ---------------------------------------------------------------- templates

def body_paragraphs(d, for_report=False):
    """for_report leaves out what the report already shows as tables (cash flow,
    fixed vs variable, the category list)."""
    paras = []

    s = f"Regular spending in {d['cur_month']} was {money(d['total'])}"
    if d['direction'] == 'none':
        s += "."
    elif d['direction'] == 'flat':
        s += f", about the same as {money(d['total_prev'])} in {d['prev_month']} ({d['pct']:+.1f}%)."
    else:
        word = 'up' if d['direction'] == 'up' else 'down'
        s += f", {word} {abs(d['pct']):.1f}% from {money(d['total_prev'])} in {d['prev_month']}."
    if d['show_mtd']:
        s += f" So far this month, through {nice_date(d['mtd_through'])}, it is {money(d['mtd'])}."
    paras.append(s)

    if not for_report:
        paras.append(cash_flow_paragraph(d))
        paras.append(fixed_variable_paragraph(d))

    parts = []
    if d['top_categories'] and d['total'] > 0 and not for_report:
        parts.append("The largest categories were " + join_list(
            f"{nice_category(c)} ({money(a)}, {a / d['total'] * 100:.0f}%)" for c, a in d['top_categories']) + ".")
    rose = [m for m in d['movers'] if m[1] > 0]
    fell = [m for m in d['movers'] if m[1] < 0]
    if rose:
        parts.append(f"Compared with {d['prev_month']}, " + join_list(
            f"{nice_category(c)} rose by {money(delta)} (from {money(b)} to {money(n)})"
            for c, delta, b, n in rose) + ".")
    if fell:
        lead = "" if rose else f"Compared with {d['prev_month']}, "
        text = join_list(f"{nice_category(c)} fell by {money(-delta)} (from {money(b)} to {money(n)})"
                         for c, delta, b, n in fell)
        parts.append((lead + text[0].lower() + text[1:] if lead else text) + ".")
    if d['spikes']:
        parts.append("Spending was unusually high on " + join_list(
            f"{nice_date(day)} ({money(amt)}; largest charge {m}, {money(a)})"
            for day, amt, m, a in d['spikes'])
            + f", against a daily average of {money(d['avg_daily'])} not counting fixed expenses.")
    if parts:
        paras.append(' '.join(parts))

    parts = []
    if d['top_account']:
        n, a = d['top_account']
        parts.append(f"Most regular spending went through {n}: {money(a)}, "
                     f"{a / d['total'] * 100:.0f}% of the total.")
    if d['top_merchants']:
        parts.append("The top merchants were " + join_list(
            f"{m} ({money(a)}, {c} transaction{'s' if c != 1 else ''})" for m, a, c in d['top_merchants']) + ".")
    if d['idle_accounts']:
        parts.append(join_list(d['idle_accounts']) + (" had" if len(d['idle_accounts']) == 1 else " each had")
                     + " no regular spending.")
    if parts:
        paras.append(' '.join(parts))

    parts = []
    if d['one_offs']:
        parts.append(f"Annual bills and other large one-time items (${ONE_OFF_THRESHOLD:,} or more), not included in the "
                     f"total above: " + join_list(
                         f"{n} {money(a)} on {nice_date(dt)}" + (" (annual bill)" if known else "")
                         for dt, n, a, known in d['one_offs']) + ".")
    if d['movement']:
        parts.append(f"Money moved between your own accounts, which is not spending (items of "
                     f"${ONE_OFF_THRESHOLD:,} or more): " + join_list(
                         f"{c} {label} totaling {money(t)}" for label, (c, t) in d['movement']) + ".")
    if parts:
        paras.append(' '.join(parts))
    return paras


def cash_flow_paragraph(d):
    cf, m = d['cash_flow'], d['cur_month']
    ins = []
    if cf['paychecks']:
        n = cf['paycheck_count']
        ins.append(f"{money(cf['paychecks'])} from {n} paycheck{'s' if n != 1 else ''}")
    if cf['other_income']:
        ins.append(f"{money(cf['other_income'])} of other income")
    if cf['from_others']:
        ins.append(f"{money(cf['from_others'])} from other people (Venmo, Zelle, deposits)")
    if cf['large_in']:
        ins.append(f"{money(cf['large_in'])} in large one-time amounts (" + join_list(
            f"{n} {money(a)} on {nice_date(dt)}" for dt, n, a in cf['large_in_items']) + ")")
    outs = [f"{money(cf['regular_spend'])} of regular spending"]
    if cf['one_off_spend']:
        outs.append(f"{money(cf['one_off_spend'])} in large one-time purchases")
    if cf['to_others']:
        big = ("; including " + join_list(f"{n} {money(a)} on {nice_date(dt)}"
                                          for dt, n, a in cf['large_out_items'])) if cf['large_out_items'] else ""
        outs.append(f"{money(cf['to_others'])} paid to other people (checks, Venmo, Zelle{big})")

    s = f"Money in for {m} was {money(cf['money_in'])}"
    s += (": " + join_list(ins) + ".") if ins else "."
    s += f" Money out was {money(cf['money_out'])}: " + join_list(outs) + "."
    if cf['net'] >= 0:
        s += f" That leaves {money(cf['net'])} more in than out."
    else:
        s += f" That is {money(-cf['net'])} more out than in."
    if cf['saved'] > 0:
        s += f" Separately, a net {money(cf['saved'])} went into savings ({SAVINGS_LABEL})."
    elif cf['saved'] < 0:
        s += f" Separately, a net {money(-cf['saved'])} came out of savings ({SAVINGS_LABEL})."
    return s


def fixed_variable_paragraph(d):
    total = d['total']
    classes = d['classes']
    if total <= 0:
        return f"There was no regular spending in {d['cur_month']}."
    phrase = {'Fixed, necessary': 'fixed and necessary', 'Fixed, discretionary': 'fixed but discretionary'}
    parts = [f"{money(amt)} ({amt / total * 100:.0f}%) was {phrase.get(cls, cls.lower())}"
             for cls, amt, _ in classes]
    s = f"Of the {money(total)} in regular spending, " + join_list(parts) + "."

    if d['classes_prev']:
        prev = {cls: (amt, dict(items)) for cls, amt, items in d['classes_prev']}
        now_var = dict((c, a) for c, a, _ in classes).get('Variable', 0.0)
        prev_var = prev.get('Variable', (0.0, {}))[0]
        if prev_var > 0:
            pct = (now_var - prev_var) / prev_var * 100
            if abs(pct) < FLAT_PCT:
                s += f" Variable spending was about the same as {d['prev_month']} ({money(prev_var)})."
            else:
                s += (f" Variable spending was {'up' if pct > 0 else 'down'} {abs(pct):.1f}% "
                      f"from {money(prev_var)} in {d['prev_month']}.")
        missing = [label for cls, _, items in classes for label, a in items
                   if a == 0 and prev.get(cls, (0, {}))[1].get(label, 0) > 0]
        if missing:
            s += (f" No charge this month for " + join_list(missing)
                  + f", which {'was' if len(missing) == 1 else 'were'} charged in {d['prev_month']}.")
    return s


# ---------------------------------------------------------------- opener

DIRECTION_PHRASE = {
    'up': 'Spending was higher than the month before.',
    'down': 'Spending was lower than the month before.',
    'flat': 'Spending was about the same as the month before.',
    'none': 'There is no earlier month to compare with.',
}
UP_WORDS = r'\b(up|higher|more|rose|rise|rising|increase[sd]?|grew|growth|jump(ed)?|climb(ed)?)\b'
DOWN_WORDS = r'\b(down|lower|less|fell|fall|drop(ped)?|decrease[sd]?|decline[sd]?|reduc\w*|cut)\b'
FLAT_WORDS = r'\b(same|steady|flat|similar|consistent|stable|unchanged)\b'
BANNED = (r'\b(balance|highest|lowest|largest|biggest|smallest|record|ever|first|last year|always|never|'
          r'most|least|should|budget|sav(e|ing|ings)|recommend|consider|try|great|good|bad|'
          r'worr\w*|concern\w*|congrat\w*|unfortunately)\b')


def check_opener(text, direction, cur_month):
    """Return a list of problems; empty means the opener is usable."""
    t = text.strip()
    low = t.lower()
    problems = []
    if not t:
        return ['empty']
    if re.search(r'[\d$%]', t):
        problems.append('contains a number')
    if len(re.findall(r'[.!?](\s|$)', t)) != 1 or not re.search(r'[.!]$', t):
        problems.append('not exactly one sentence')
    if len(t.split()) > 25:
        problems.append('too long')
    if re.search(r'[*#\n"]', t):
        problems.append('formatting or quotes')
    if cur_month.lower() not in low:
        problems.append('does not name the month')
    if m := re.search(BANNED, low):
        problems.append(f'banned word "{m.group(0)}"')
    up, down, flat = (bool(re.search(p, low)) for p in (UP_WORDS, DOWN_WORDS, FLAT_WORDS))
    if direction == 'up' and (down or flat or not up):
        problems.append('direction does not match "up"')
    if direction == 'down' and (up or flat or not down):
        problems.append('direction does not match "down"')
    if direction == 'flat' and (up or down):
        problems.append('direction does not match "about the same"')
    if direction == 'none' and (up or down or flat):
        problems.append('mentions a trend when there is no comparison')
    return problems


def opener_prompt(d):
    return f"""Write ONE short, plain sentence that opens a personal spending summary for {d['cur_month']}.

What you know: {DIRECTION_PHRASE[d['direction']]}

Rules:
- Exactly one sentence, under 20 words, addressed to the reader as "you".
- Mention {d['cur_month']}.
- No numbers, no dollar amounts, no percentages, no category, merchant or account names.
- No advice, no praise or judgment, no superlatives, no guesses about causes.
- Output only the sentence."""


def fallback_opener(d):
    return {
        'up': f"Your spending in {d['cur_month']} was higher than in {d['prev_month']}.",
        'down': f"Your spending in {d['cur_month']} was lower than in {d['prev_month']}.",
        'flat': f"Your spending in {d['cur_month']} was about the same as in {d['prev_month']}.",
        'none': f"Here is your spending summary for {d['cur_month']}.",
    }[d['direction']]


def make_opener(d, log=None):
    """Returns (sentence, source) where source is 'model' or 'fallback: <reason>'."""
    prompt = opener_prompt(d)
    if log:
        log("OPENER PROMPT GIVEN TO THE MODEL:\n" + prompt + "\n")
    try:
        import ollama
    except ImportError:
        return fallback_opener(d), 'fallback: ollama package not installed'
    last = 'no attempts'
    for attempt in range(1, OPENER_ATTEMPTS + 1):
        try:
            text = ollama.generate(model=MODEL, prompt=prompt)['response'].strip()
        except Exception as e:  # Ollama not running, model missing, etc.
            return fallback_opener(d), f'fallback: model call failed ({type(e).__name__})'
        problems = check_opener(text, d['direction'], d['cur_month'])
        if log:
            log(f"attempt {attempt}: {text!r} -> {'OK' if not problems else '; '.join(problems)}")
        if not problems:
            return text, 'model'
        last = '; '.join(problems)
    return fallback_opener(d), f'fallback: {OPENER_ATTEMPTS} attempts rejected (last: {last})'


# ---------------------------------------------------------------- entry points

def build_narrative(today=None, log=None):
    """Everything the report needs: {'title', 'opener', 'opener_source', 'paragraphs', 'data'}."""
    today = today or date.today()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            d = compute(cur, today)
    finally:
        conn.close()
    opener, source = make_opener(d, log=log)
    return {
        'title': f"Spending summary — {d['cur_name']}",
        'opener': opener,
        'opener_source': source,
        'paragraphs': body_paragraphs(d),
        'report_paragraphs': body_paragraphs(d, for_report=True),
        'data': d,
    }


if __name__ == '__main__':
    show = '--facts' in sys.argv
    result = build_narrative(log=print if show else None)
    if show:
        print(f"\nopener source: {result['opener_source']}\n")
    print(result['title'] + "\n")
    print(result['opener'] + "\n")
    print("\n\n".join(result['paragraphs']))
