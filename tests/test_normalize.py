from datetime import date
from decimal import Decimal

from invoice_agents.normalize import (
    normalize_amount,
    normalize_date,
    normalize_invoice_number,
    normalize_item_name,
    normalize_ocr_digits,
    normalize_vendor_name,
)


def test_inv_1012_invoice_number():
    number, corrections = normalize_invoice_number("INV 1012")

    assert number == "INV-1012"
    assert corrections == ["INV 1012 -> INV-1012"]


def test_plain_invoice_digits():
    number, corrections = normalize_invoice_number("1002")

    assert number == "INV-1002"
    assert corrections == ["1002 -> INV-1002"]


def test_canonical_invoice_number_has_no_correction():
    number, corrections = normalize_invoice_number("INV-1012")

    assert number == "INV-1012"
    assert corrections == []


def test_letter_o_used_as_zero():
    text, corrections = normalize_ocr_digits("2O26")

    assert text == "2026"
    assert corrections == ["2O26 -> 2026"]


def test_amount_with_letter_o():
    amount, corrections = normalize_amount("3,500.O0")

    assert amount == Decimal("3500.00")
    assert type(amount) is Decimal
    assert corrections == ["3,500.O0 -> 3500.00"]


def test_normalize_amount_missing_or_unreadable():
    missing, missing_notes = normalize_amount(None)
    blank, blank_notes = normalize_amount("")
    text, text_notes = normalize_amount("N/A")

    assert missing is None
    assert blank is None
    assert text is None
    assert missing_notes == ["unreadable amount: null"]
    assert blank_notes == ["unreadable amount: ''"]
    assert text_notes == ["unreadable amount: 'N/A'"]


def test_widget_a_item_name():
    name, corrections = normalize_item_name("Widget A")

    assert name == "WidgetA"
    assert corrections == ["Widget A -> WidgetA"]


def test_gadget_x_item_name():
    name, corrections = normalize_item_name("Gadget X")

    assert name == "GadgetX"
    assert corrections == ["Gadget X -> GadgetX"]


def test_widget_b_needs_no_correction():
    name, corrections = normalize_item_name("WidgetB")

    assert name == "WidgetB"
    assert corrections == []


def test_vendor_name_is_unchanged():
    name, corrections = normalize_vendor_name("QuickShip Distributers")

    assert name == "QuickShip Distributers"
    assert corrections == []


def test_ocr_invoice_date():
    parsed, corrections = normalize_date("26-Jan-2O26")

    assert parsed == date(2026, 1, 26)
    assert corrections == ["26-Jan-2O26 -> 2026-01-26"]
