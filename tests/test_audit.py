import json

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

    append_decision(audit_path, first)
    append_decision(audit_path, second)

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == first
    assert json.loads(lines[1]) == second
    assert read_decisions(audit_path) == [first, second]
