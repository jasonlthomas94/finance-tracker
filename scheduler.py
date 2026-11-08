"""Runs the pipeline on schedule. launchd starts this at login, every 30
minutes, and at the scheduled times; it only does work when a run is due.

  Weekly:  every Monday from 07:00 -> sync, checks, report, notification.
  Monthly: every 4th from 07:00    -> the same, then email the full report.
           A monthly run also counts as that week's weekly run.

"Due" means the job has not succeeded since its most recent scheduled time,
so a run missed while the Mac was asleep, off or locked happens at the next
check after it is back. After a failure it waits RETRY_HOURS before trying
again (so a broken bank login does not notify every 30 minutes).

    python scheduler.py                    # what launchd runs
    python scheduler.py --status           # show what is due
    python scheduler.py --run weekly       # run now regardless of schedule
    python scheduler.py --run monthly --no-email
    python scheduler.py --run monthly --note updated   # adds '(updated)' to the subject
    python scheduler.py --test-email       # email the current report to REPORT_EMAIL_CC only
    python scheduler.py --request-test-email  # same, but on the next launchd run

Email settings live in .env: REPORT_EMAIL_FROM (the Mail account to send
from), REPORT_EMAIL_TO (recipient), optional REPORT_EMAIL_CC.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(HERE, 'venv', 'bin', 'python')
STATE_DIR = os.path.join(HERE, 'output', 'state')
LOG_DIR = os.path.join(HERE, 'output', 'logs')
SENT_DIR = os.path.join(HERE, 'output', 'sent')
STATE_FILE = os.path.join(STATE_DIR, 'runs.json')
REPORT = os.path.join(HERE, 'output', 'report.html')
SUMMARY = os.path.join(HERE, 'output', 'summary.txt')

WEEKLY_WEEKDAY = 0      # Monday
MONTHLY_DAY = 4
RUN_HOUR = 7
RETRY_HOURS = 3

load_dotenv(os.path.join(HERE, '.env'))


# ---------------------------------------------------------------- schedule

def last_weekly_time(now):
    d = now - timedelta(days=(now.weekday() - WEEKLY_WEEKDAY) % 7)
    t = d.replace(hour=RUN_HOUR, minute=0, second=0, microsecond=0)
    return t if t <= now else t - timedelta(days=7)


def last_monthly_time(now):
    t = now.replace(day=MONTHLY_DAY, hour=RUN_HOUR, minute=0, second=0, microsecond=0)
    if t > now:
        prev = (now.replace(day=1) - timedelta(days=1))
        t = prev.replace(day=MONTHLY_DAY, hour=RUN_HOUR, minute=0, second=0, microsecond=0)
    return t


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def _ts(state, key):
    v = state.get(key)
    return datetime.fromisoformat(v) if v else datetime.min


def due_job(now, state):
    """'monthly', 'weekly' or None."""
    if _ts(state, 'monthly_ok') < last_monthly_time(now):
        return 'monthly'
    if _ts(state, 'weekly_ok') < last_weekly_time(now):
        return 'weekly'
    return None


def in_backoff(now, state):
    return now - _ts(state, 'last_failure') < timedelta(hours=RETRY_HOURS)


# ---------------------------------------------------------------- steps

def notify(title, message):
    script = 'on run argv\n display notification (item 2 of argv) with title (item 1 of argv)\nend run'
    subprocess.run(['osascript', '-', title, message], input=script, text=True, capture_output=True)


def run_step(name, args, log):
    log.write(f"\n=== {name} ({datetime.now():%Y-%m-%d %H:%M:%S})\n")
    log.flush()
    r = subprocess.run([PYTHON] + args, cwd=HERE, stdout=log, stderr=subprocess.STDOUT)
    log.write(f"=== {name} exit {r.returncode}\n")
    log.flush()
    return r.returncode == 0


MAIL_SCRIPT = '''on run argv
    set fromAddr to item 1 of argv
    set toAddr to item 2 of argv
    set ccAddr to item 3 of argv
    set subj to item 4 of argv
    set bodyText to item 5 of argv
    set filePath to item 6 of argv
    tell application "Mail"
        set msg to make new outgoing message with properties {subject:subj, content:bodyText & return & return, visible:false, sender:fromAddr}
        tell msg
            if toAddr is not "" then make new to recipient at end of to recipients with properties {address:toAddr}
            if ccAddr is not "" then make new cc recipient at end of cc recipients with properties {address:ccAddr}
            tell content to make new attachment with properties {file name:(POSIX file filePath) as alias} at after last paragraph
        end tell
        delay 3
        send msg
    end tell
end run
'''


def email_report(month_label, to_addr, cc_addr, log):
    from_addr = os.getenv('REPORT_EMAIL_FROM', '')
    if not from_addr or not (to_addr or cc_addr):
        log.write("Email skipped: REPORT_EMAIL_FROM and REPORT_EMAIL_TO must be set in .env\n")
        return False
    os.makedirs(SENT_DIR, exist_ok=True)
    attachment = os.path.join(SENT_DIR, f"How are we doing - {month_label}.html")
    shutil.copyfile(REPORT, attachment)
    try:
        with open(SUMMARY) as f:
            summary = f.read().strip()
    except OSError:
        summary = ''
    body = (f"{summary}\n\nThe full report is attached. Open the attachment in a web browser to see it "
            f"(on a phone, tap it and open it in Safari or Chrome).")
    r = subprocess.run(['osascript', '-', from_addr, to_addr, cc_addr,
                        f"How are we doing? {month_label}", body, attachment],
                       input=MAIL_SCRIPT, text=True, capture_output=True)
    log.write(f"\n=== email to {to_addr or '-'} cc {cc_addr or '-'}: exit {r.returncode}\n{r.stderr}\n")
    return r.returncode == 0


def report_month_label():
    from queries import month_periods
    start, _, _, _ = month_periods(datetime.now().date())
    return f"{start:%B %Y}"


def run_job(job, send_email=True, subject_note=''):
    now = datetime.now()
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"{now:%Y-%m-%d_%H%M}_{job}.log")
    state = load_state()
    with open(log_path, 'w') as log:
        ok = run_step('sync', ['sync_transactions.py'], log)
        if not ok:
            fail(state, now, f"{job}: sync failed", log_path)
            notify("Finance report: sync failed", "Probably a bank login. Open the log in output/logs.")
            return False
        ok = run_step('report', ['generate_report.py'], log)
        if not ok:
            fail(state, now, f"{job}: report failed (checks or error)", log_path)
            notify("Finance report: not written", "A check failed. Open the log in output/logs.")
            return False
        attention = ''
        with open(log_path) as f:
            for line in f:
                if line.startswith('Needs attention:'):
                    attention = line.split(':', 1)[1].strip()
        if job == 'monthly' and send_email:
            month = report_month_label() + (f" ({subject_note})" if subject_note else '')
            if not email_report(month, os.getenv('REPORT_EMAIL_TO', ''), os.getenv('REPORT_EMAIL_CC', ''), log):
                fail(state, now, "monthly: email failed", log_path)
                notify("Finance report: email not sent", "The report was written but not emailed. See output/logs.")
                return False
    state['weekly_ok'] = now.isoformat(timespec='seconds')
    if job == 'monthly':
        state['monthly_ok'] = now.isoformat(timespec='seconds')
    state.pop('last_failure', None)
    state.pop('last_failure_msg', None)
    state['last_log'] = log_path
    save_state(state)
    sent = " and emailed" if job == 'monthly' and send_email else ""
    notify("Finance report ready", f"{job.capitalize()} report written{sent}. Needs attention: {attention or '0'}.")
    return True


def fail(state, now, msg, log_path):
    state['last_failure'] = now.isoformat(timespec='seconds')
    state['last_failure_msg'] = msg
    state['last_log'] = log_path
    save_state(state)


def main(argv):
    now = datetime.now()
    state = load_state()
    if '--status' in argv:
        print(f"now: {now:%Y-%m-%d %H:%M}")
        print(f"last weekly ok:  {state.get('weekly_ok', 'never')} (due after {last_weekly_time(now)})")
        print(f"last monthly ok: {state.get('monthly_ok', 'never')} (due after {last_monthly_time(now)})")
        print(f"due now: {due_job(now, state) or 'nothing'}")
        if state.get('last_failure'):
            print(f"last failure: {state['last_failure']} {state.get('last_failure_msg', '')}"
                  + (" (waiting before retry)" if in_backoff(now, state) else ""))
        print(f"last log: {state.get('last_log', '-')}")
        return 0
    if '--test-email' in argv:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, f"{now:%Y-%m-%d_%H%M}_test-email.log"), 'w') as log:
            cc = os.getenv('REPORT_EMAIL_CC', '') or os.getenv('REPORT_EMAIL_FROM', '')
            ok = email_report(f"{report_month_label()} (test)", '', cc, log)
        print("test email sent" if ok else "test email FAILED (see output/logs)")
        return 0 if ok else 1
    if '--request-test-email' in argv:
        state['test_email_requested'] = True
        save_state(state)
        print("Test email requested; run: launchctl kickstart gui/$(id -u)/com.financetracker.report")
        return 0
    if '--run' in argv:
        job = argv[argv.index('--run') + 1]
        note = argv[argv.index('--note') + 1] if '--note' in argv else ''
        return 0 if run_job(job, send_email='--no-email' not in argv, subject_note=note) else 1
    if state.pop('test_email_requested', None):
        # Set by `python scheduler.py --request-test-email`, so the test runs
        # under launchd and macOS asks for Mail permission for this job.
        save_state(state)
        return main(['--test-email'])
    job = due_job(now, state)
    if job is None or in_backoff(now, state):
        return 0
    return 0 if run_job(job) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
