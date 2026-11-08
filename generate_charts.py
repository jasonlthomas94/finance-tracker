"""Charts for the last complete calendar month.

    python generate_charts.py   # writes PNGs to output/

`build_charts(cur, today)` returns {filename: png_bytes} for the HTML report.
"""
import io
import os
from datetime import date, timedelta

import matplotlib
matplotlib.use('Agg')  # no display needed
import matplotlib.pyplot as plt

from queries import (
    get_connection, get_spend_by_household, get_spend_by_account, get_daily_spend,
    month_periods, month_label, nice_category, get_monthly_overview
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'output')


def _png(fig):
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format='png', dpi=150)
    plt.close(fig)
    return buf.getvalue()


def build_charts(cur, today=None):
    today = today or date.today()
    start, end, _, _ = month_periods(today)
    period_name = month_label(start)
    plt.style.use('seaborn-v0_8-whitegrid')
    charts = {}

    by_category = [(c, a) for c, a, _ in get_spend_by_household(cur, start, end)]
    if by_category:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.barh([nice_category(c) for c, _ in by_category],
                [float(a) for _, a in by_category], color='#4C72B0')
        ax.set_xlabel('Spend ($)')
        ax.set_title(f'Spend by Category — {period_name}')
        ax.invert_yaxis()
        charts['category_spend.png'] = _png(fig)

    by_account = get_spend_by_account(cur, start, end)
    if by_account:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.bar([a for a, _ in by_account], [float(v) for _, v in by_account], color='#55A868')
        ax.set_ylabel('Spend ($)')
        ax.set_title(f'Spend by Account/Card — {period_name}')
        plt.setp(ax.get_xticklabels(), rotation=20, ha='right')
        charts['account_spend.png'] = _png(fig)

    # Every day of the month, with $0 for days without spend, so the line
    # does not skip quiet days.
    spend = {d: float(a) for d, a in get_daily_spend(cur, start, end)}
    if spend:
        days = [start + timedelta(days=i) for i in range((end - start).days)]
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(days, [spend.get(d, 0.0) for d in days], marker='o',
                color='#C44E52', linewidth=1.5, markersize=4)
        ax.set_ylabel('Spend ($)')
        ax.set_title(f'Daily Spend — {period_name} (includes fixed bills)')
        plt.setp(ax.get_xticklabels(), rotation=45, ha='right')
        charts['daily_spend.png'] = _png(fig)

    rows, _ = get_monthly_overview(cur, today)
    if rows:
        charts['savings_by_month.png'] = savings_chart(rows)

    return charts


def savings_chart(rows):
    """Per month: what was left over (in minus out) next to the net amount
    that went into (+) or came out of (-) savings."""
    labels = [f"{m:%b}" for m, _ in rows]
    net = [cf['net'] for _, cf in rows]
    saved = [cf['saved'] for _, cf in rows]
    x = range(len(rows))
    w = 0.38
    fig, ax = plt.subplots(figsize=(10, 4.5))
    b1 = ax.bar([i - w / 2 for i in x], net, w, color='#8C8C8C', label='Left over (money in minus money out)')
    b2 = ax.bar([i + w / 2 for i in x], saved, w,
                color=['#3A8E57' if v >= 0 else '#C44E52' for v in saved],
                label='Into savings (green) / out of savings (red)')
    ax.axhline(0, color='#333333', linewidth=0.8)
    for bars in (b1, b2):
        for bar in bars:
            v = bar.get_height()
            ax.annotate(f"{'-' if v < 0 else ''}${abs(v):,.0f}", (bar.get_x() + bar.get_width() / 2, v),
                        ha='center', va='bottom' if v >= 0 else 'top', fontsize=8,
                        xytext=(0, 3 if v >= 0 else -3), textcoords='offset points')
    ax.set_xticks(list(x), labels)
    ax.set_ylabel('$')
    ax.set_title('Month by month: left over vs. savings')
    ax.legend(loc='upper left', fontsize=8, frameon=False)
    ax.margins(y=0.15)
    return _png(fig)


if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            charts = build_charts(cur)
    finally:
        conn.close()
    for name, data in charts.items():
        with open(os.path.join(OUTPUT_DIR, name), 'wb') as f:
            f.write(data)
        print(f"Saved {name}")
    print(f"\nCharts saved to {OUTPUT_DIR}")
