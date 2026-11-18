from decimal import Decimal

import pytest

from invoice_agents.llm import GrokLlm, MockLlm, _normalize_draft
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


def test_normalize_draft_money_strings_become_decimals():
    draft = _normalize_draft(
        {
            "subtotal": "$5,000.00",
            "tax_amount": "$5,000.00",
            "total": "$5,000.00",
        }
    )

    for key in ("subtotal", "tax_amount", "total"):
        assert draft[key] == Decimal("5000.00")
        assert type(draft[key]) is Decimal
    assert draft["corrections"] == [
        "$5,000.00 -> 5000.00",
        "$5,000.00 -> 5000.00",
        "$5,000.00 -> 5000.00",
    ]


def test_normalize_draft_int_and_float_amounts():
    draft = _normalize_draft(
        {
            "subtotal": 5000,
            "tax_amount": 0.0,
            "total": 250.5,
            "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": 250}],
        }
    )

    assert draft["subtotal"] == Decimal("5000.00")
    assert type(draft["subtotal"]) is Decimal
    assert draft["tax_amount"] == Decimal("0.00")
    assert draft["total"] == Decimal("250.50")
    assert draft["line_items"][0]["unit_price"] == "250.00"


def test_normalize_draft_nan_amount_is_unreadable():
    draft = _normalize_draft(
        {
            "tax_amount": float("nan"),
            "total": float("inf"),
        }
    )

    assert draft["tax_amount"] is None
    assert draft["total"] is None
    assert "tax_amount unreadable: nan" in draft["corrections"]
    assert "total unreadable: inf" in draft["corrections"]


def test_normalize_draft_null_tax_amount_is_left_empty():
    draft = _normalize_draft(
        {
            "invoice_number": "INV-1001",
            "subtotal": "5000.00",
            "tax_amount": None,
            "total": "5000.00",
        }
    )

    assert draft["tax_amount"] is None
    assert draft["total"] == Decimal("5000.00")
    assert "tax_amount unreadable: null" in draft["corrections"]


def _install_failing_client(monkeypatch, error: Exception) -> None:
    class FakeChat:
        def create(self, **kwargs):
            raise error

    class FakeClient:
        def __init__(self, api_key, timeout):
            assert api_key == "test-key"
            assert timeout == 120
            self.chat = FakeChat()

    monkeypatch.setattr("xai_sdk.Client", FakeClient)


@pytest.mark.parametrize("key", [None, ""])
def test_missing_api_key_exits_without_calling_grok(monkeypatch, key):
    if key is None:
        monkeypatch.delenv("XAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("XAI_API_KEY", key)

    def fail_client(*args, **kwargs):
        raise AssertionError("Grok client was constructed")

    monkeypatch.setattr("xai_sdk.Client", fail_client)

    with pytest.raises(SystemExit) as caught:
        GrokLlm()

    assert caught.value.code == "XAI_API_KEY is not set."


def test_grok_timeout_exits_immediately(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    _install_failing_client(monkeypatch, RuntimeError("DEADLINE_EXCEEDED"))
    llm = GrokLlm()

    with pytest.raises(SystemExit) as caught:
        llm._ask("hello")

    assert caught.value.code == "Grok call timed out after 120s."


@pytest.mark.parametrize("status", ["UNAUTHENTICATED", "PERMISSION_DENIED"])
def test_rejected_api_key_exits_immediately(monkeypatch, status):
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    _install_failing_client(monkeypatch, RuntimeError(status))
    llm = GrokLlm()

    with pytest.raises(SystemExit) as caught:
        llm._ask("hello")

    assert caught.value.code == "Grok rejected the API key. Check XAI_API_KEY."


def test_other_grok_error_names_the_reason(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    _install_failing_client(monkeypatch, RuntimeError("connection reset"))
    llm = GrokLlm()

    with pytest.raises(SystemExit) as caught:
        llm._ask("hello")

    assert caught.value.code == "Grok call failed: connection reset"
