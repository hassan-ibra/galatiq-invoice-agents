from datetime import date
from decimal import Decimal

from invoice_agents.agents.approval import approve
from invoice_agents.audit import read_decisions
from invoice_agents.models import Finding, Invoice, LineItem, PipelineState


def _invoice(
    number: str = "INV-1001",
    total: str = "5000.00",
    **overrides,
) -> Invoice:
    data = {
        "invoice_number": number,
        "vendor_name": "Widgets Inc.",
        "vendor_address": "100 Main St",
        "invoice_date": date(2026, 1, 15),
        "due_date": date(2026, 2, 1),
        "currency": "USD",
        "line_items": [
            LineItem(item="WidgetA", quantity=1, unit_price=Decimal(total)),
        ],
        "total": Decimal(total),
    }
    data.update(overrides)
    return Invoice(**data)


def _run(tmp_path, invoice: Invoice, name: str, **overrides) -> PipelineState:
    # Approval runs after validation, which has already set this.
    fields = {
        "source_path": f"{invoice.invoice_number}.txt",
        "db_path": str(tmp_path / "inventory.db"),
        "audit_path": str(tmp_path / f"{name}.jsonl"),
        "invoice": invoice,
        "status": "approved",
    }
    fields.update(overrides)
    state = PipelineState(**fields)
    result = approve(state)
    assert result.summary
    assert invoice.invoice_number in result.summary
    assert result.critique
    return result


def _by_rule(state: PipelineState, rule: int) -> list[Finding]:
    return [finding for finding in state.findings if finding.rule == rule]


def test_row10_address_warning_holds(tmp_path):
    warned = _run(
        tmp_path,
        _invoice(address_warnings=["Suspicious vendor address: 100 Main St"]),
        "warned",
    )
    warnings = _by_rule(warned, 10)

    assert warned.status == "hold"
    assert len(warnings) == 1
    assert warnings[0].outcome == "hold"
    assert not any(finding.outcome == "reject" for finding in warned.findings)

    ordinary = _run(
        tmp_path,
        _invoice(vendor_address="742 Evergreen Terrace"),
        "ordinary",
    )
    assert ordinary.status == "approved"
    assert _by_rule(ordinary, 10) == []


def test_row11_default_as_of_is_not_late(tmp_path):
    state = _run(tmp_path, _invoice(), "default")

    assert state.as_of is None
    assert state.late is False
    assert state.status == "approved"
    assert _by_rule(state, 11) == []

    same_day = _run(
        tmp_path,
        _invoice("INV-SAME", due_date=date(2026, 1, 15)),
        "same-day",
    )
    assert same_day.late is False
    assert same_day.status == "approved"
    assert _by_rule(same_day, 11) == []

    missing_due = _run(
        tmp_path,
        _invoice("INV-NODUE", due_date=None),
        "missing-due",
    )
    assert missing_due.late is False
    assert missing_due.status == "approved"
    assert _by_rule(missing_due, 11) == []


def test_row11_as_of_marks_late_but_payable(tmp_path):
    state = _run(tmp_path, _invoice(), "late", as_of=date(2026, 2, 2))
    late_findings = _by_rule(state, 11)

    assert state.late is True
    assert state.status == "approved"
    assert len(late_findings) == 1
    assert late_findings[0].outcome == "info"
    assert not any(finding.outcome in {"reject", "hold"} for finding in state.findings)


def test_row12_over_10000_needs_a_person(tmp_path):
    over = _run(tmp_path, _invoice("INV-OVER", "10000.01"), "over")
    gate = _by_rule(over, 12)

    assert over.status == "hold"
    assert "hold" in over.summary
    assert len(gate) == 1
    assert gate[0].outcome == "hold"
    assert "10000.01" in gate[0].reason
    assert "person" in gate[0].reason.lower()

    exact = _run(tmp_path, _invoice("INV-EXACT", "10000.00"), "exact")
    assert exact.status == "approved"
    assert _by_rule(exact, 12) == []

    euros = _run(
        tmp_path,
        _invoice("INV-1014", "15000.00", currency="EUR"),
        "euros",
    )
    assert euros.status == "approved"
    assert _by_rule(euros, 12) == []


def test_row12_auto_approve_skips_only_that_gate(tmp_path):
    skipped = _run(
        tmp_path,
        _invoice("INV-OVER", "10000.01"),
        "skipped",
        auto_approve=True,
    )
    reasons = " ".join(decision.get("reason", "") for decision in read_decisions(skipped.audit_path))

    assert skipped.status == "approved"
    assert _by_rule(skipped, 12) == []
    assert "skipped" in reasons.lower()

    held = _run(
        tmp_path,
        _invoice("INV-1002", "15000.00", vendor_name="Gadgets Co."),
        "held",
        auto_approve=True,
        status="hold",
        findings=[
            Finding(
                rule=1,
                outcome="hold",
                reason="GadgetX quantity 20 exceeds stock 5",
            )
        ],
    )
    stock = _by_rule(held, 1)
    dollars = _by_rule(held, 12)

    assert held.status == "hold"
    assert len(stock) == 1
    assert stock[0].outcome == "hold"
    assert len(dollars) == 1
    assert dollars[0].outcome == "info"


def test_approval_keeps_a_rejection(tmp_path):
    state = _run(
        tmp_path,
        _invoice("INV-1003", "100000.00", vendor_name="Fraudster LLC"),
        "rejected",
        status="rejected",
        findings=[
            Finding(rule=5, outcome="reject", reason="FakeItem has zero stock"),
        ],
    )
    dollars = _by_rule(state, 12)

    assert state.status == "rejected"
    assert "INV-1003" in state.summary
    assert state.critique
    assert any(finding.rule == 5 and finding.outcome == "reject" for finding in state.findings)
    assert len(dollars) == 1
    assert dollars[0].outcome == "info"
