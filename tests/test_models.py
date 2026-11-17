from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from invoice_agents.models import Finding, Invoice, LineItem, PipelineState


def _line(**overrides):
    data = {
        "item": "WidgetA",
        "quantity": 10,
        "unit_price": Decimal("250.00"),
    }
    data.update(overrides)
    return LineItem(**data)


def _invoice(**overrides):
    data = {
        "invoice_number": "INV-1001",
        "vendor_name": "Widgets Inc.",
        "line_items": [_line()],
        "total": Decimal("2500.00"),
    }
    data.update(overrides)
    return Invoice(**data)


def _state(**overrides):
    data = {
        "source_path": "data/invoices/invoice_1001.txt",
        "db_path": "data/inventory.db",
        "audit_path": "logs/audit.jsonl",
    }
    data.update(overrides)
    return PipelineState(**data)


def test_missing_line_amount_is_quantity_times_unit_price():
    line = _line()

    assert line.amount == Decimal("2500.00")
    assert type(line.amount) is Decimal


def test_stated_line_amount_is_kept():
    line = _line(amount=Decimal("2499.99"))

    assert line.amount == Decimal("2499.99")


def test_money_fields_stay_decimal():
    line = _line(note="Volume discount")
    invoice = _invoice(
        line_items=[line],
        subtotal=Decimal("2500.00"),
        tax_amount=Decimal("0.00"),
        total=Decimal("2500.00"),
    )

    assert type(line.unit_price) is Decimal
    assert type(line.amount) is Decimal
    assert type(invoice.subtotal) is Decimal
    assert type(invoice.tax_amount) is Decimal
    assert type(invoice.total) is Decimal
    assert line.note == "Volume discount"


def test_float_money_is_rejected():
    with pytest.raises(ValidationError):
        _line(unit_price=250.00)
    with pytest.raises(ValidationError):
        _line(amount=2500.00)
    with pytest.raises(ValidationError):
        _invoice(subtotal=2500.00)
    with pytest.raises(ValidationError):
        _invoice(tax_amount=0.00)
    with pytest.raises(ValidationError):
        _invoice(total=2500.00)


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        _line(sku="nope")
    with pytest.raises(ValidationError):
        _invoice(vendor_id="nope")
    with pytest.raises(ValidationError):
        Finding(rule=4, outcome="reject", reason="short by 110.00", score=1)
    with pytest.raises(ValidationError):
        _state(region="US")


def test_invoice_defaults_and_empty_vendor_name():
    invoice = _invoice(vendor_name="")

    assert invoice.vendor_name == ""
    assert invoice.revision is None
    assert invoice.vendor_address is None
    assert invoice.invoice_date is None
    assert invoice.due_date is None
    assert invoice.currency == "USD"
    assert invoice.subtotal is None
    assert invoice.tax_amount == Decimal("0")
    assert type(invoice.tax_amount) is Decimal
    assert invoice.payment_terms is None
    assert invoice.notes is None
    assert invoice.source_files == []
    assert invoice.corrections == []
    assert invoice.address_warnings == []


def test_invoice_dates_are_dates():
    invoice = _invoice(
        invoice_date=date(2026, 1, 15),
        due_date=date(2026, 2, 1),
        revision="R1",
    )

    assert invoice.invoice_date == date(2026, 1, 15)
    assert invoice.due_date == date(2026, 2, 1)
    assert invoice.revision == "R1"


def test_finding_rule_must_be_1_through_12():
    Finding(rule=1, outcome="hold", reason="requested 20, stock 5")
    Finding(rule=12, outcome="info", reason="total is over 10000.00")

    with pytest.raises(ValidationError):
        Finding(rule=0, outcome="reject", reason="no such row")
    with pytest.raises(ValidationError):
        Finding(rule=13, outcome="reject", reason="no such row")
    with pytest.raises(ValidationError):
        Finding(rule=4, outcome="approve", reason="not an outcome")


def test_pipeline_state_starts_pending():
    state = _state()

    assert state.invoice is None
    assert state.findings == []
    assert state.status == "pending"
    assert state.late is False
    assert state.summary == ""
    assert state.critique == ""
    assert state.as_of is None
    assert state.auto_approve is False
    assert state.use_llm is False
    assert state.void_original is False
    assert state.payment_result is None


def test_apply_findings_reject_wins():
    state = _state(
        findings=[
            Finding(rule=1, outcome="hold", reason="requested 20, stock 5"),
            Finding(rule=4, outcome="reject", reason="stated minus expected is -110.00"),
            Finding(rule=12, outcome="info", reason="total is over 10000.00"),
        ]
    )

    state.apply_findings()

    assert state.status == "rejected"


def test_apply_findings_hold_when_no_reject():
    state = _state(
        findings=[
            Finding(rule=9, outcome="hold", reason="300.00 is above catalog 250.00"),
            Finding(rule=11, outcome="info", reason="due date is late"),
        ]
    )

    state.apply_findings()

    assert state.status == "hold"


def test_apply_findings_approved_when_no_reject_or_hold():
    empty = _state()
    empty.apply_findings()
    assert empty.status == "approved"

    info_only = _state(
        findings=[
            Finding(rule=11, outcome="info", reason="due date is not before the as-of day"),
        ]
    )
    info_only.apply_findings()
    assert info_only.status == "approved"
