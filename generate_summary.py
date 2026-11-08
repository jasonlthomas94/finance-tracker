from datetime import date
from queries import (
    get_connection, get_large_one_offs, get_spend_by_household,
    get_spend_by_account, get_top_merchants, get_total_spend,
    ONE_OFF_THRESHOLD, HISTORY_START, month_periods, month_label
)

conn = get_connection()
cur = conn.cursor()

today = date.today()
cur_start, cur_end, prev_start, prev_end = month_periods(today)
cur_name, prev_name = month_label(cur_start), month_label(prev_start)

if prev_start < HISTORY_START:
    print(f"\n⚠️  {prev_name} starts before history begins ({HISTORY_START}); "
          f"do not treat the comparison as a real trend.")

one_offs_current = get_large_one_offs(cur, cur_start, cur_end)
one_offs_prev = get_large_one_offs(cur, prev_start, prev_end)
current_by_category = [(c, a) for c, a, _ in get_spend_by_household(cur, cur_start, cur_end)]
by_account = get_spend_by_account(cur, cur_start, cur_end)
top_merchants = get_top_merchants(cur, cur_start, cur_end)
total_current = get_total_spend(cur, cur_start, cur_end)
total_prev = get_total_spend(cur, prev_start, prev_end)
total_mtd = get_total_spend(cur, cur_end, today)

print(f"\nTotal regular spend {cur_name}: ${total_current:.2f}")
print(f"Total regular spend {prev_name}: ${total_prev:.2f}")
print(f"Month to date ({cur_end} to {today}, excluding today): ${total_mtd:.2f}")

if one_offs_current:
    print(f"\nNotable one-time items {cur_name} (${ONE_OFF_THRESHOLD}+, excluded from totals above):")
    for name, amt, txn_date, category in one_offs_current:
        print(f"  {txn_date} | {name} | ${amt:.2f} | {category or 'Uncategorized'}")

if one_offs_prev:
    print(f"\nNotable one-time items {prev_name} (${ONE_OFF_THRESHOLD}+, for context, excluded from that month's total):")
    for name, amt, txn_date, category in one_offs_prev:
        print(f"  {txn_date} | {name} | ${amt:.2f} | {category or 'Uncategorized'}")

print(f"\nBy category ({cur_name}, regular spend only):")
for cat, amt in current_by_category:
    print(f"  {cat or 'Uncategorized'}: ${amt:.2f}")
print(f"\nBy account/card ({cur_name}, regular spend only):")
for name, amt in by_account:
    print(f"  {name}: ${amt:.2f}")
print(f"\nTop merchants ({cur_name}, regular spend only):")
for name, amt, count in top_merchants:
    print(f"  {name}: ${amt:.2f} ({count} transactions)")

cur.close()
conn.close()
