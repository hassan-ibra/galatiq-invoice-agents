from datetime import date
from decimal import Decimal
from pathlib import Path

from invoice_agents.agents.ingestion import ingest
from invoice_agents.audit import read_decisions
from invoice_agents.llm import MockLlm
from invoice_agents.models import PipelineState
from invoice_agents.parsers import parse_csv, parse_json, parse_xml, pdf_text

INVOICES = Path(__file__).resolve().parents[1] / "data" / "invoices"


def _ingest(path: Path, tmp_path: Path) -> PipelineState:
    state = PipelineState(
        source_path=str(path),
        db_path=str(tmp_path / "inventory.db"),
        audit_path=str(tmp_path / "audit.jsonl"),
    )
    return ingest(state)


def test_parse_failure_is_rejected_and_logged(tmp_path):
    bad = tmp_path / "invoice_bad.txt"
    bad.write_text("Vendor: Nobody\nInvoice Number: INV-1098\n", encoding="utf-8")

    state = _ingest(bad, tmp_path)
    decisions = read_decisions(tmp_path / "audit.jsonl")

    assert state.status == "rejected"
    assert state.invoice is None
    assert len(decisions) == 1
    assert decisions[0]["status"] == "rejected"
    assert decisions[0]["reasons"] == ["invoice total is missing"]


def test_inv_1001_corrections_are_recorded_once(tmp_path):
    state = _ingest(INVOICES / "invoice_1001.txt", tmp_path)
    invoice = state.invoice

    assert invoice is not None
    assert invoice.corrections.count("5,000.00 -> 5000.00") == 1
    assert len(invoice.corrections) == len(set(invoice.corrections))


def test_row7_ocr_invoice_is_normalized(tmp_path):
    state = _ingest(INVOICES / "invoice_1012.txt", tmp_path)
    invoice = state.invoice

    assert invoice is not None
    assert invoice.invoice_number == "INV-1012"
    assert invoice.invoice_date == date(2026, 1, 26)
    assert invoice.vendor_name == "QuickShip Distributers"
    assert [(line.item, line.quantity, line.unit_price) for line in invoice.line_items] == [
        ("WidgetA", 12, Decimal("250.00")),
        ("WidgetB", 7, Decimal("500.00")),
        ("GadgetX", 4, Decimal("750.00")),
    ]
    assert invoice.total == Decimal("9975.00")
    assert type(invoice.total) is Decimal
    assert invoice.tax_amount == Decimal("475.00")
    assert invoice.corrections
    assert state.status == "pending"


def test_row8_matching_copies_process_once(tmp_path):
    for name in ("invoice_1011.txt", "invoice_1011.pdf"):
        state = _ingest(INVOICES / name, tmp_path)
        invoice = state.invoice
        assert invoice is not None
        assert invoice.total == Decimal("3000.00")
        assert type(invoice.total) is Decimal
        assert {Path(path).name for path in invoice.source_files} == {
            "invoice_1011.txt",
            "invoice_1011.pdf",
        }
        assert state.status == "pending"


def test_row8_disagreement_holds(tmp_path):
    first = tmp_path / "invoice_a.txt"
    second = tmp_path / "invoice_b.txt"
    _write_copy(first, "10.00")
    _write_copy(second, "12.00")

    state = _ingest(first, tmp_path)

    assert state.status == "hold"
    assert any(
        finding.rule == 8 and finding.outcome == "hold" and "total" in finding.reason
        for finding in state.findings
    )


def test_row2_revised_file_is_selected(tmp_path):
    for name in ("invoice_1004.json", "invoice_1004_revised.json"):
        state = _ingest(INVOICES / name, tmp_path)
        invoice = state.invoice
        assert invoice is not None
        assert invoice.revision == "R1"
        assert invoice.total == Decimal("5940.00")
        assert type(invoice.total) is Decimal
        assert state.void_original is True
        assert state.status == "pending"


def test_parse_json_amounts_are_decimal():
    draft = parse_json((INVOICES / "invoice_1004.json").read_text(encoding="utf-8"))

    assert draft["total"] == Decimal("1890.00")
    assert type(draft["total"]) is Decimal
    assert type(draft["line_items"][0]["unit_price"]) is Decimal


def test_parse_csv_pairs_amounts_are_decimal():
    draft = parse_csv((INVOICES / "invoice_1006.csv").read_text(encoding="utf-8"))

    assert draft["invoice_number"] == "INV-1006"
    assert draft["total"] == Decimal("2750.00")
    assert type(draft["total"]) is Decimal
    assert type(draft["line_items"][0]["unit_price"]) is Decimal
    assert draft["line_items"][0]["quantity"] == 5


def test_parse_csv_table_amounts_are_decimal():
    draft = parse_csv((INVOICES / "invoice_1007.csv").read_text(encoding="utf-8"))

    assert draft["invoice_number"] == "INV-1007"
    assert draft["total"] == Decimal("15525.00")
    assert type(draft["total"]) is Decimal
    assert type(draft["tax_amount"]) is Decimal
    assert draft["line_items"][0]["quantity"] == 20


def test_parse_xml_amounts_are_decimal():
    draft = parse_xml((INVOICES / "invoice_1014.xml").read_text(encoding="utf-8"))

    assert draft["invoice_number"] == "INV-1014"
    assert draft["currency"] == "EUR"
    assert draft["total"] == Decimal("4125.00")
    assert type(draft["total"]) is Decimal
    assert type(draft["line_items"][0]["unit_price"]) is Decimal


def test_parse_email_invoice_uses_vendor_line():
    draft = MockLlm().extract((INVOICES / "invoice_1008.txt").read_text(encoding="utf-8"))

    assert draft["vendor_name"] == "NoProd Industries"
    assert [line["item"] for line in draft["line_items"]] == ["SuperGizmo", "MegaSprocket"]
    assert draft["total"] == Decimal("9900.00")
    assert type(draft["total"]) is Decimal


def test_parse_abbreviated_text_amounts_are_decimal():
    draft = MockLlm().extract((INVOICES / "invoice_1002.txt").read_text(encoding="utf-8"))

    assert draft["invoice_number"] == "INV-1002"
    assert draft["vendor_name"] == "Gadgets Co."
    assert draft["total"] == Decimal("15000.00")
    assert type(draft["total"]) is Decimal
    assert draft["line_items"][0]["item"] == "GadgetX"
    assert draft["line_items"][0]["quantity"] == 20
    assert draft["line_items"][0]["unit_price"] == Decimal("750.00")
    assert type(draft["line_items"][0]["unit_price"]) is Decimal


def test_parse_pdf_amounts_are_decimal():
    draft = MockLlm().extract(pdf_text(INVOICES / "invoice_1011.pdf"))

    assert draft["invoice_number"] == "INV-1011"
    assert draft["total"] == Decimal("3000.00")
    assert type(draft["total"]) is Decimal
    assert type(draft["line_items"][0]["unit_price"]) is Decimal
    assert draft["line_items"][0]["quantity"] == 6


def _write_copy(path: Path, total: str) -> None:
    path.write_text(
        "\n".join(
            [
                "INVOICE",
                "",
                "Vendor: Example Co.",
                "Invoice Number: INV-1099",
                "Date: 2026-01-01",
                "Due Date: 2026-02-01",
                "",
                "Items:",
                "  WidgetA    qty: 1    unit price: $10.00",
                "",
                "Subtotal: $10.00",
                "Tax (0%): $0.00",
                f"Total Amount: ${total}",
                "",
            ]
        ),
        encoding="utf-8",
    )
