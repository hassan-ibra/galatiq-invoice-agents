import re
from decimal import Decimal

from invoice_agents.agents.validation import validate
from invoice_agents.models import Invoice, LineItem, PipelineState
from setup_inventory import create_inventory


def _line(item: str, quantity: int, unit_price: str) -> LineItem:
    return LineItem(
        item=item,
        quantity=quantity,
        unit_price=Decimal(unit_price),
    )


def _invoice(number: str, lines: list[LineItem], total: str, **overrides) -> Invoice:
    data = {
        "invoice_number": number,
        "vendor_name": "Vendor",
        "line_items": lines,
        "total": Decimal(total),
    }
    if "subtotal" in overrides:
        subtotal = overrides.pop("subtotal")
        data["subtotal"] = None if subtotal is None else Decimal(subtotal)
    if "tax_amount" in overrides:
        data["tax_amount"] = Decimal(overrides.pop("tax_amount"))
    data.update(overrides)
    return Invoice(**data)


def _run(tmp_path, invoice: Invoice) -> PipelineState:
    db_path = tmp_path / "inventory.db"
    create_inventory(db_path)
    state = PipelineState(
        source_path=f"{invoice.invoice_number}.txt",
        db_path=str(db_path),
        audit_path=str(tmp_path / "audit.jsonl"),
        invoice=invoice,
    )
    return validate(state)


def _reasons(state: PipelineState, rule: int) -> list[str]:
    return [finding.reason for finding in state.findings if finding.rule == rule]


def test_row1_quantity_over_stock_holds(tmp_path):
    over = _run(
        tmp_path,
        _invoice(
            "INV-1002",
            [_line("GadgetX", 20, "750.00")],
            "15000.00",
            subtotal="15000.00",
            vendor_name="Gadgets Co.",
        ),
    )
    over_reason = " ".join(_reasons(over, 1))
    assert over.status == "hold"
    assert re.search(r"\b20\b", over_reason)
    assert re.search(r"\b5\b", over_reason)

    summed = _run(
        tmp_path,
        _invoice(
            "INV-SUM",
            [_line("WidgetA", 10, "250.00"), _line("WidgetA", 6, "250.00")],
            "4000.00",
            subtotal="4000.00",
        ),
    )
    assert summed.status == "hold"
    assert re.search(r"\b16\b", " ".join(_reasons(summed, 1)))

    within_stock = _run(
        tmp_path,
        _invoice(
            "INV-1005",
            [
                _line("WidgetA", 14, "250.00"),
                _line("GadgetX", 8, "750.00"),
                _line("WidgetB", 10, "500.00"),
            ],
            "15225.00",
            subtotal="14500.00",
            tax_amount="725.00",
            vendor_name="Global Supply Chain Partners",
        ),
    )
    assert not any("WidgetB" in reason for reason in _reasons(within_stock, 1))

    fake = _run(
        tmp_path,
        _invoice(
            "INV-1003",
            [_line("FakeItem", 100, "1000.00")],
            "100000.00",
            subtotal="100000.00",
            vendor_name="Fraudster LLC",
        ),
    )
    assert _reasons(fake, 1) == []


def test_row3_non_usd_holds(tmp_path):
    eur = _run(
        tmp_path,
        _invoice(
            "INV-1014",
            [_line("WidgetA", 4, "225.00"), _line("WidgetB", 6, "475.00")],
            "4125.00",
            subtotal="3750.00",
            tax_amount="375.00",
            currency="EUR",
            vendor_name="TechParts International",
        ),
    )
    assert eur.status == "hold"
    assert _reasons(eur, 3)
    assert _reasons(eur, 9) == []

    expensive_eur = _run(
        tmp_path,
        _invoice(
            "INV-1014-HIGH",
            [_line("WidgetA", 1, "300.00")],
            "300.00",
            subtotal="300.00",
            currency="EUR",
        ),
    )
    assert expensive_eur.status == "hold"
    assert _reasons(expensive_eur, 9) == []


def test_row4_total_mismatch_rejects(tmp_path):
    short = _run(
        tmp_path,
        _invoice(
            "INV-1007",
            [
                _line("WidgetA", 20, "250.00"),
                _line("WidgetB", 15, "500.00"),
                _line("GadgetX", 3, "750.00"),
            ],
            "15525.00",
            subtotal="14750.00",
            tax_amount="885.00",
            vendor_name="MegaWidgets Corp",
        ),
    )
    over = _run(
        tmp_path,
        _invoice(
            "INV-1013",
            [
                _line("WidgetA", 15, "250.00"),
                _line("WidgetB", 10, "500.00"),
                _line("GadgetX", 5, "750.00"),
                _line("WidgetA", 5, "240.00"),
                _line("WidgetB", 8, "480.00"),
                _line("GadgetX", 3, "750.00"),
                _line("WidgetA", 2, "250.00"),
                _line("GadgetX", 1, "750.00"),
            ],
            "22562.80",
            subtotal="21040.00",
            tax_amount="1472.80",
            vendor_name="Atlas Industrial Supply",
        ),
    )

    assert short.status == "rejected"
    assert any("-110.00" in reason for reason in _reasons(short, 4))
    assert over.status == "rejected"
    assert any("50.00" in reason for reason in _reasons(over, 4))


def test_row5_unknown_or_zero_stock_rejects(tmp_path):
    unknown = _run(
        tmp_path,
        _invoice(
            "INV-1008",
            [_line("SuperGizmo", 12, "400.00"), _line("MegaSprocket", 6, "850.00")],
            "9900.00",
            subtotal="9900.00",
            vendor_name="NoProd Industries",
        ),
    )
    missing_one = _run(
        tmp_path,
        _invoice(
            "INV-1016",
            [
                _line("WidgetA", 4, "250.00"),
                _line("WidgetB", 2, "500.00"),
                _line("WidgetC", 3, "350.00"),
            ],
            "3233.00",
            subtotal="3050.00",
            tax_amount="183.00",
            vendor_name="Widgets Inc.",
        ),
    )
    zero_stock = _run(
        tmp_path,
        _invoice(
            "INV-1003",
            [_line("FakeItem", 100, "1000.00")],
            "100000.00",
            subtotal="100000.00",
            vendor_name="Fraudster LLC",
        ),
    )

    unknown_reason = " ".join(_reasons(unknown, 5))
    assert unknown.status == "rejected"
    assert "SuperGizmo" in unknown_reason
    assert "MegaSprocket" in unknown_reason
    assert missing_one.status == "rejected"
    assert "WidgetC" in " ".join(_reasons(missing_one, 5))
    assert zero_stock.status == "rejected"
    assert "FakeItem" in " ".join(_reasons(zero_stock, 5))
    assert _reasons(zero_stock, 1) == []


def test_row6_negative_quantity_rejects(tmp_path):
    state = _run(
        tmp_path,
        _invoice(
            "INV-1009",
            [_line("WidgetA", -5, "250.00"), _line("WidgetB", 2, "500.00")],
            "-250.00",
            subtotal="1000.00",
            tax_amount="0.00",
            vendor_name="",
        ),
    )
    reason = " ".join(_reasons(state, 6))

    assert state.status == "rejected"
    assert "-5" in reason
    assert "1000.00" in reason
    assert "-250.00" in reason


def test_row9_high_price_holds_shipping_allowed(tmp_path):
    state = _run(
        tmp_path,
        _invoice(
            "INV-1010",
            [
                _line("WidgetA", 8, "250.00"),
                _line("WidgetB", 4, "500.00"),
                _line("GadgetX", 2, "750.00"),
                _line("WidgetA", 4, "300.00"),
                _line("Shipping", 1, "150.00"),
            ],
            "7185.00",
            subtotal="6700.00",
            tax_amount="335.00",
            vendor_name="Consolidated Materials Group",
        ),
    )
    assert state.status == "hold"
    assert any(
        finding.rule == 9 and "WidgetA" in finding.reason and "300.00" in finding.reason
        for finding in state.findings
    )
    assert _reasons(state, 5) == []

    exact = _run(
        tmp_path,
        _invoice(
            "INV-EXACT",
            [_line("WidgetA", 1, "275.00")],
            "275.00",
            subtotal="275.00",
        ),
    )
    assert exact.status == "approved"
    assert exact.findings == []

    just_over = _run(
        tmp_path,
        _invoice(
            "INV-OVER",
            [_line("WidgetA", 1, "275.01")],
            "275.01",
            subtotal="275.01",
        ),
    )
    assert just_over.status == "hold"
    assert _reasons(just_over, 9)

    discount = _run(
        tmp_path,
        _invoice(
            "INV-DISCOUNT",
            [_line("WidgetA", 1, "240.00"), _line("WidgetB", 1, "480.00")],
            "720.00",
            subtotal="720.00",
        ),
    )
    assert discount.status == "approved"
    assert _reasons(discount, 9) == []


def test_clean_invoice_is_approved(tmp_path):
    state = _run(
        tmp_path,
        _invoice(
            "INV-1001",
            [_line("WidgetA", 10, "250.00"), _line("WidgetB", 5, "500.00")],
            "5000.00",
            subtotal="5000.00",
            tax_amount="0.00",
            vendor_name="Widgets Inc.",
        ),
    )

    assert state.findings == []
    assert state.status == "approved"
