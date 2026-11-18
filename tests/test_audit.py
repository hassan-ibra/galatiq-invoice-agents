import json
from datetime import datetime

from invoice_agents.audit import append_decision, read_decisions


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
