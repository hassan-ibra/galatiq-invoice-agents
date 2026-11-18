import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from invoice_agents.audit import append_decision, format_audit_log, read_decisions

ROOT = Path(__file__).resolve().parents[1]

SHOW_LOG = """\
{"ts": "2026-11-18T19:38:03.530338+00:00", "agent": "ingestion", "run_id": "3f2a1c", "attempt": 1, "invoice_number": "INV-1002", "problems": ["invoice_date invalid"]}
{"ts": "2026-11-18T19:38:04.100000+00:00", "agent": "validation", "run_id": "3f2a1c", "invoice_number": "INV-1002", "outcome": "hold", "reason": "GadgetX quantity 20 exceeds stock 5"}
{"ts": "2026-11-18T19:38:05.100000+00:00", "agent": "payment", "run_id": "3f2a1c", "invoice_number": "INV-1002", "status": "hold", "paid": false, "reason": "not paid: invoice is on hold"}
{"ts": "2026-11-18T20:02:59.293544+00:00", "agent": "payment", "run_id": "ab12cd", "invoice_number": "INV-1001", "paid": true, "amount": "5000.00", "vendor": "Widgets Inc.", "reason": "Paid 5000.00 to Widgets Inc."}
"""

SHOW_TEXT = """\
=== Run 3f2a1c | INV-1002 | 2026-11-18 19:38 UTC ===
  ingestion: attempt 1, problems: invoice_date invalid
  validation: hold - GadgetX quantity 20 exceeds stock 5
  payment: not paid - invoice is on hold

=== Run ab12cd | INV-1001 | 2026-11-18 20:02 UTC ===
  payment: Paid 5000.00 to Widgets Inc.
"""


def test_appends_two_decisions_and_reads_them_back(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    first = {
        "invoice_number": "INV-1001",
        "status": "approved",
        "reason": "Paid 5000.00 to Widgets Inc.",
    }
    second = {
        "invoice_number": "INV-1003",
        "status": "rejected",
        "reason": "FakeItem has zero stock.",
    }

    append_decision(audit_path, first, agent="payment")
    append_decision(audit_path, second, agent="validation")

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    written = [json.loads(line) for line in lines]
    assert len(written) == 2
    assert _without_stamp(written[0]) == first
    assert _without_stamp(written[1]) == second
    assert written[0]["agent"] == "payment"
    assert written[1]["agent"] == "validation"
    for decision in written:
        stamp = datetime.fromisoformat(decision["ts"])
        assert stamp.utcoffset() is not None
        assert stamp.utcoffset().total_seconds() == 0
    assert read_decisions(audit_path) == written


def _without_stamp(decision: dict) -> dict:
    return {key: value for key, value in decision.items() if key not in {"ts", "agent"}}


def test_show_log_groups_runs_by_run_id(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text(SHOW_LOG, encoding="utf-8")

    assert format_audit_log(path) == SHOW_TEXT.rstrip("\n")

    completed = subprocess.run(
        [sys.executable, "main.py", f"--show-log={path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == SHOW_TEXT
    assert json.loads(SHOW_LOG.splitlines()[0])["run_id"] == "3f2a1c"
