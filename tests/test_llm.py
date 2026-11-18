from decimal import Decimal

from invoice_agents.llm import MockLlm
from invoice_agents.models import Finding, Invoice, LineItem


def _invoice() -> Invoice:
    return Invoice(
        invoice_number="INV-1001",
        vendor_name="Widgets Inc.",
        line_items=[
            LineItem(item="WidgetA", quantity=10, unit_price=Decimal("250.00")),
        ],
        total=Decimal("2500.00"),
    )


def test_mock_flags_famous_addresses():
    llm = MockLlm()

    pennsylvania = llm.flag_address("1600 Pennsylvania Ave, Washington, DC 20500")
    evergreen = llm.flag_address("742 Evergreen Terrace, Springfield, IL 62704")
    shouted = llm.flag_address("1600 PENNSYLVANIA AVE")

    assert len(pennsylvania) == 1
    assert len(evergreen) == 1
    assert len(shouted) == 1
    assert "1600 Pennsylvania Ave" in pennsylvania[0]
    assert "742 Evergreen Terrace" in evergreen[0]


def test_mock_does_not_flag_ordinary_address():
    llm = MockLlm()

    assert llm.flag_address("100 Main St, Chicago, IL 60601") == []
    assert llm.flag_address(None) == []
    assert llm.flag_address("") == []


def test_summarize_and_critique_pair():
    llm = MockLlm()
    invoice = _invoice()

    summary = llm.summarize(invoice, [])
    critique = llm.critique(summary)

    assert llm.summarize_calls == 1
    assert llm.critique_calls == 1
    assert "INV-1001" in summary
    assert "approved" in summary
    assert "INV-1001" in critique
    assert "approved" in critique

    rejected = MockLlm().summarize(
        invoice,
        [Finding(rule=5, outcome="reject", reason="FakeItem has zero stock")],
    )
    assert "INV-1001" in rejected
    assert "rejected" in rejected
