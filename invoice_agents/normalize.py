import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

CATALOG_ITEMS = ("WidgetA", "WidgetB", "GadgetX", "FakeItem", "Shipping")


def _correction(original: str, canonical: str) -> list[str]:
    if original == canonical:
        return []
    return [f"{original} -> {canonical}"]


def _replace_o_with_zero(value: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(value):
        if char in "Oo" and _o_is_zero(value, index):
            chars.append("0")
        else:
            chars.append(char)
    return "".join(chars)


def _o_is_zero(value: str, index: int) -> bool:
    neighbors: list[str] = []
    if index > 0:
        neighbors.append(value[index - 1])
    if index + 1 < len(value):
        neighbors.append(value[index + 1])
    return any(char.isdigit() or char in ",.$" for char in neighbors)


def normalize_ocr_digits(value: str) -> tuple[str, list[str]]:
    fixed = _replace_o_with_zero(value)
    return fixed, _correction(value, fixed)


def normalize_invoice_number(value: str) -> tuple[str, list[str]]:
    fixed = _replace_o_with_zero(value.strip())
    match = re.fullmatch(r"(?i:INV)?[\s-]*(\d+)", fixed)
    if match is None:
        return value, []
    canonical = f"INV-{match.group(1)}"
    return canonical, _correction(value, canonical)


def normalize_item_name(value: str) -> tuple[str, list[str]]:
    compact = re.sub(r"\s+", "", value)
    for name in CATALOG_ITEMS:
        if compact.lower() == name.lower():
            return name, _correction(value, name)
    return value, []


def normalize_vendor_name(value: str) -> tuple[str, list[str]]:
    return value, []


def normalize_amount(value: str | None) -> tuple[Decimal | None, list[str]]:
    if value is None:
        return None, ["unreadable amount: null"]
    if not isinstance(value, str) or value.strip() == "":
        shown = "''" if isinstance(value, str) else repr(value)
        return None, [f"unreadable amount: {shown}"]
    try:
        fixed = _replace_o_with_zero(value)
        cleaned = fixed.replace(",", "").replace("$", "").strip()
        if cleaned == "":
            return None, [f"unreadable amount: {value!r}"]
        amount = Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None, [f"unreadable amount: {value!r}"]
    return amount, _correction(value, f"{amount:.2f}")


def normalize_date(value: str) -> tuple[date, list[str]]:
    fixed = _replace_o_with_zero(value.strip())
    parsed = datetime.strptime(fixed, "%d-%b-%Y").date()
    return parsed, _correction(value, parsed.isoformat())
