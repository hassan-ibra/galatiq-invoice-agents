from decimal import Decimal
from pathlib import Path

from invoice_agents.agents.payment import mock_payment, pay
from invoice_agents.audit import read_decisions
from invoice_agents.models import Finding, Invoice, LineItem, PipelineState

VOID_REASON = (
    "original INV-1004 with no revision is voided because revision R1 replaces it, "
    "and Acme never pays twice"
)


def _invoice(number: str, total: str, **overrides) -> Invoice:
    data = {
        "invoice_number": number,
        "vendor_name": "Widgets Inc.",
        "vendor_address": "100 Main St",
        "line_items": [
            LineItem(item="WidgetA", quantity=1, unit_price=Decimal(total)),
        ],
        "total": Decimal(total),
    }
    data.update(overrides)
    return Invoice(**data)


def _run(tmp_path, invoice: Invoice, name: str, **overrides) -> PipelineState:
    state = PipelineState(
        source_path=f"{invoice.invoice_number}.txt",
        db_path=str(tmp_path / "inventory.db"),
        audit_path=str(tmp_path / f"{name}.jsonl"),
        invoice=invoice,
        **overrides,
    )
    return pay(state)


def _spy(monkeypatch):
    calls = []
    real = mock_payment

    def spy(vendor, amount):
        calls.append((vendor, amount))
        return real(vendor, amount)

    monkeypatch.setattr("invoice_agents.agents.payment.mock_payment", spy)
    return calls


def _reasons(audit_path: str) -> str:
    return " ".join(decision.get("reason", "") for decision in read_decisions(audit_path))


def _finding_was_not_logged(audit_path: str, reason: str) -> bool:
    path = Path(audit_path)
    if not path.exists():
        return True
    return reason not in path.read_text(encoding="utf-8")


def test_approved_invoice_is_paid(tmp_path, monkeypatch, capsys):
    calls = _spy(monkeypatch)

    state = _run(
        tmp_path,
        _invoice("INV-1001", "5000.00", vendor_name="Widgets Inc."),
        "paid",
        status="approved",
    )

    assert calls == [("Widgets Inc.", Decimal("5000.00"))]
    assert state.payment_result == {
        "status": "success",
        "vendor": "Widgets Inc.",
        "amount": "5000.00",
    }
    assert capsys.readouterr().err == "Paid 5000.00 to Widgets Inc.\n"


def test_rejected_invoice_is_not_paid(tmp_path, monkeypatch):
    calls = _spy(monkeypatch)
    reason = "FakeItem has zero stock"

    state = _run(
        tmp_path,
        _invoice("INV-1003", "100000.00", vendor_name="Fraudster LLC"),
        "rejected",
        status="rejected",
        findings=[Finding(rule=5, outcome="reject", reason=reason)],
    )

    assert calls == []
    assert state.payment_result is None
    assert _finding_was_not_logged(state.audit_path, reason)


def test_held_invoice_is_not_paid(tmp_path, monkeypatch):
    calls = _spy(monkeypatch)
    reason = "WidgetA unit price 300.00 is above catalog 250.00"

    state = _run(
        tmp_path,
        _invoice("INV-1010", "7185.00", vendor_name="Consolidated Materials Group"),
        "held",
        status="hold",
        findings=[Finding(rule=9, outcome="hold", reason=reason)],
    )

    assert calls == []
    assert state.payment_result is None
    assert _finding_was_not_logged(state.audit_path, reason)


def test_row2_void_original_and_pay_revised_once(tmp_path, monkeypatch, capsys):
    calls = _spy(monkeypatch)
    revised = _invoice(
        "INV-1004",
        "5940.00",
        vendor_name="Precision Parts Ltd.",
        vendor_address="100 Main St",
        revision="R1",
    )

    paid = _run(tmp_path, revised, "revised", status="approved", void_original=True)
    revised_log = read_decisions(paid.audit_path)

    assert calls == [("Precision Parts Ltd.", Decimal("5940.00"))]
    assert paid.payment_result == {
        "status": "success",
        "vendor": "Precision Parts Ltd.",
        "amount": "5940.00",
    }
    assert revised_log[0]["reason"] == VOID_REASON

    held = _run(
        tmp_path,
        _invoice(
            "INV-1004",
            "5940.00",
            vendor_name="Precision Parts Ltd.",
            vendor_address="742 Evergreen Terrace, Springfield, IL 62704",
            revision="R1",
            address_warnings=["Suspicious vendor address: 742 Evergreen Terrace"],
        ),
        "address-hold",
        status="hold",
        void_original=True,
        findings=[
            Finding(
                rule=10,
                outcome="hold",
                reason="vendor address flagged for human review: 742 Evergreen Terrace",
            )
        ],
    )
    held_log = read_decisions(held.audit_path)

    assert calls == [("Precision Parts Ltd.", Decimal("5940.00"))]
    assert held.payment_result is None
    assert held_log[0]["reason"] == VOID_REASON
    assert _finding_was_not_logged(held.audit_path, "742 Evergreen Terrace")
    assert "Paid" not in _reasons(held.audit_path)

    again = pay(
        PipelineState(
            source_path=paid.source_path,
            db_path=paid.db_path,
            audit_path=paid.audit_path,
            invoice=revised,
            status="approved",
            void_original=True,
        )
    )

    assert calls == [("Precision Parts Ltd.", Decimal("5940.00"))]
    assert again.payment_result is None
    assert "already paid" in _reasons(again.audit_path).lower()
    assert capsys.readouterr().err == "Paid 5940.00 to Precision Parts Ltd.\n"
