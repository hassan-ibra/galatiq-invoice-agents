import csv
import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

from invoice_agents.normalize import (
    normalize_amount,
    normalize_date,
    normalize_invoice_number,
    normalize_item_name,
    normalize_vendor_name,
)

CENT = Decimal("0.01")


def parse_json(text: str) -> dict:
    payload = json_loads(text)
    corrections: list[str] = []
    vendor = payload.get("vendor") or {}
    if isinstance(vendor, str):
        vendor_name = vendor
        vendor_address = None
    else:
        vendor_name = vendor.get("name") or ""
        vendor_address = vendor.get("address")
    number, found = normalize_invoice_number(str(payload["invoice_number"]))
    corrections.extend(found)
    vendor_name, found = normalize_vendor_name(str(vendor_name))
    corrections.extend(found)
    return _draft(
        invoice_number=number,
        revision=payload.get("revision"),
        vendor_name=vendor_name,
        vendor_address=vendor_address,
        invoice_date=_optional_date(payload.get("date"), corrections),
        due_date=_optional_date(payload.get("due_date"), corrections),
        currency=payload.get("currency"),
        line_items=[_json_line(raw, corrections) for raw in payload.get("line_items") or []],
        subtotal=_optional_money(payload.get("subtotal"), corrections),
        tax_amount=_optional_money(payload.get("tax_amount"), corrections),
        total=_money(payload["total"], corrections),
        payment_terms=payload.get("payment_terms") or None,
        notes=payload.get("notes"),
        corrections=corrections,
    )


def parse_csv(text: str) -> dict:
    rows = list(csv.reader(io.StringIO(text)))
    if rows and [cell.strip().lower() for cell in rows[0][:2]] == ["field", "value"]:
        return _parse_csv_pairs(rows)
    return _parse_csv_table(rows)


def parse_xml(text: str) -> dict:
    root = ElementTree.fromstring(text)
    header = root.find("header")
    totals = root.find("totals")
    corrections: list[str] = []
    number, found = normalize_invoice_number(_xml_text(header, "invoice_number"))
    corrections.extend(found)
    vendor_name, found = normalize_vendor_name(_xml_text(header, "vendor"))
    corrections.extend(found)
    lines = []
    for item in root.findall("./line_items/item"):
        name, found = normalize_item_name(_xml_text(item, "name"))
        corrections.extend(found)
        lines.append(
            {
                "item": name,
                "quantity": int(_xml_text(item, "quantity")),
                "unit_price": _money(_xml_text(item, "unit_price"), corrections),
                "amount": None,
                "note": None,
            }
        )
    return _draft(
        invoice_number=number,
        revision=None,
        vendor_name=vendor_name,
        vendor_address=None,
        invoice_date=_optional_date(_xml_text(header, "date"), corrections),
        due_date=_optional_date(_xml_text(header, "due_date"), corrections),
        currency=_xml_text(header, "currency") or None,
        line_items=lines,
        subtotal=_optional_money(_xml_text(totals, "subtotal"), corrections),
        tax_amount=_optional_money(_xml_text(totals, "tax_amount"), corrections),
        total=_money(_xml_text(totals, "total"), corrections),
        payment_terms=_xml_text(root, "payment_terms") or None,
        notes=None,
        corrections=corrections,
    )


def parse_text(text: str) -> dict:
    corrections: list[str] = []
    number_raw = _search(
        r"(?:invoice(?:[ \t]*number)?|inv(?:oice)?(?:[ \t]*no\.?)?|inv\s*#)\s*[:#]?[ \t]*(INV[\s\-]*\d+|\d+)",
        text,
    )
    number, found = normalize_invoice_number(number_raw or "")
    corrections.extend(found)
    vendor_raw = _search(r"^\s*(?:vendor|vndr)\s*:[ \t]*(.+)$", text)
    if vendor_raw is None:
        vendor_raw = _search(r"^\s*from\s*:[ \t]*(.+)$", text)
    if vendor_raw:
        vendor_raw = re.split(r"\s+due\s*:", vendor_raw, maxsplit=1, flags=re.I)[0].strip()
    vendor_name, found = normalize_vendor_name(vendor_raw or "")
    corrections.extend(found)
    without_due = re.sub(r"due(?:[ \t]*(?:date|dt))?\s*:[ \t]*[^\n]+", " ", text, flags=re.I)
    date_raw = _search(r"(?:^|\s)(?:date|dt)\s*:[ \t]*([^\n]+)", without_due)
    due_raw = _search(r"(?:^|\s)due(?:[ \t]*(?:date|dt))?\s*:[ \t]*([^\n]+)", text)
    invoice_date, found = parse_date(date_raw) if date_raw else (None, [])
    corrections.extend(found)
    if due_raw and due_raw.casefold() == "yesterday" and invoice_date is not None:
        due_date = invoice_date - timedelta(days=1)
        corrections.append(f"yesterday -> {due_date.isoformat()}")
    else:
        due_date, found = parse_date(due_raw) if due_raw else (None, [])
        corrections.extend(found)
    currency = _search(r"\bcurrency\s*:[ \t]*([A-Za-z]{3})\b", text)
    lines = [_text_line(line, corrections) for line in text.splitlines()]
    lines = [line for line in lines if line is not None]
    return _draft(
        invoice_number=number,
        revision=None,
        vendor_name=vendor_name,
        vendor_address=None,
        invoice_date=invoice_date,
        due_date=due_date,
        currency=currency.upper() if currency else None,
        line_items=lines,
        subtotal=_optional_money(_money_search(r"subtotal\s*:[ \t]*\$?([0-9O][0-9O,.]*)", text), corrections),
        tax_amount=_optional_money(
            _money_search(r"tax(?:[ \t]*\([^)]*\))?\s*:[ \t]*\$?([0-9O][0-9O,.]*)", text),
            corrections,
        ),
        total=_money(_total_search(text), corrections),
        payment_terms=_search(r"^(?:payment terms|pymnt terms|terms)\s*:[ \t]*(.+)$", text),
        notes=_search(r"^notes\s*:[ \t]*(.+)$", text),
        corrections=corrections,
    )


def pdf_text(path: str | Path) -> str:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def parse_date(value: str | None) -> tuple[date | None, list[str]]:
    if value is None:
        return None, []
    text = value.strip()
    if not text or text.casefold() in {"null", "none", "yesterday"}:
        return None, []
    try:
        return normalize_date(text)
    except ValueError:
        pass
    iso = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if iso:
        parsed = date.fromisoformat(iso.group(0))
        return parsed, _date_correction(text, parsed)
    us = re.search(r"\d{1,2}/\d{1,2}/\d{4}", text)
    if us:
        parsed = datetime.strptime(us.group(0), "%m/%d/%Y").date()
        return parsed, _date_correction(text, parsed)
    for pattern, fmt in (
        (r"[A-Za-z]+ \d{1,2}, \d{4}", "%B %d, %Y"),
        (r"[A-Za-z]+ \d{1,2} \d{4}", "%B %d %Y"),
        (r"[A-Za-z]+ \d{1,2} \d{4}", "%b %d %Y"),
    ):
        match = re.search(pattern, text)
        if match is None:
            continue
        try:
            parsed = datetime.strptime(match.group(0), fmt).date()
        except ValueError:
            continue
        return parsed, _date_correction(match.group(0), parsed)
    return None, []


def json_loads(text: str) -> dict:
    import json

    return json.loads(text, parse_float=Decimal)


def _parse_csv_pairs(rows: list[list[str]]) -> dict:
    fields: dict[str, str] = {}
    lines: list[dict] = []
    current: dict = {}
    for row in rows[1:]:
        if len(row) < 2:
            continue
        key = row[0].strip().lower()
        value = row[1].strip()
        if key == "item":
            if current:
                lines.append(current)
            current = {"item": value}
            continue
        if key in {"quantity", "unit_price"} and current:
            current[key] = value
            continue
        if current:
            lines.append(current)
            current = {}
        fields[key] = value
    if current:
        lines.append(current)
    corrections: list[str] = []
    number, found = normalize_invoice_number(fields.get("invoice_number", ""))
    corrections.extend(found)
    vendor_name, found = normalize_vendor_name(fields.get("vendor", ""))
    corrections.extend(found)
    parsed_lines = []
    for raw in lines:
        item, found = normalize_item_name(raw["item"])
        corrections.extend(found)
        parsed_lines.append(
            {
                "item": item,
                "quantity": int(raw["quantity"]),
                "unit_price": _money(raw["unit_price"], corrections),
                "amount": None,
                "note": None,
            }
        )
    return _draft(
        invoice_number=number,
        revision=None,
        vendor_name=vendor_name,
        vendor_address=None,
        invoice_date=_optional_date(fields.get("date"), corrections),
        due_date=_optional_date(fields.get("due_date"), corrections),
        currency=fields.get("currency"),
        line_items=parsed_lines,
        subtotal=_optional_money(fields.get("subtotal"), corrections),
        tax_amount=_optional_money(fields.get("tax") or fields.get("tax_amount"), corrections),
        total=_money(fields["total"], corrections),
        payment_terms=fields.get("payment_terms"),
        notes=fields.get("notes"),
        corrections=corrections,
    )


def _parse_csv_table(rows: list[list[str]]) -> dict:
    header = [cell.strip().lower() for cell in rows[0]]
    invoice_idx = _column(header, "invoice number")
    vendor_idx = _column(header, "vendor")
    date_idx = _column(header, "date")
    due_idx = _column(header, "due date")
    item_idx = _column(header, "item")
    qty_idx = _column(header, "qty", "quantity")
    price_idx = _column(header, "unit price")
    amount_idx = _column(header, "line total", "amount")
    corrections: list[str] = []
    lines = []
    fields: dict[str, str] = {}
    for row in rows[1:]:
        summary = _summary_cell(row)
        if summary is not None:
            label, value = summary
            if label.startswith("subtotal"):
                fields["subtotal"] = value
            elif label.startswith("tax"):
                fields["tax"] = value
            elif "total" in label:
                fields["total"] = value
            continue
        item = _cell(row, item_idx)
        if not item:
            continue
        if not fields:
            fields["invoice_number"] = _cell(row, invoice_idx)
            fields["vendor"] = _cell(row, vendor_idx)
            fields["date"] = _cell(row, date_idx)
            fields["due_date"] = _cell(row, due_idx)
        name, found = normalize_item_name(item)
        corrections.extend(found)
        amount = _cell(row, amount_idx)
        lines.append(
            {
                "item": name,
                "quantity": int(_cell(row, qty_idx)),
                "unit_price": _money(_cell(row, price_idx), corrections),
                "amount": _money(amount, corrections) if amount else None,
                "note": None,
            }
        )
    number, found = normalize_invoice_number(fields.get("invoice_number", ""))
    corrections.extend(found)
    vendor_name, found = normalize_vendor_name(fields.get("vendor", ""))
    corrections.extend(found)
    return _draft(
        invoice_number=number,
        revision=None,
        vendor_name=vendor_name,
        vendor_address=None,
        invoice_date=_optional_date(fields.get("date"), corrections),
        due_date=_optional_date(fields.get("due_date"), corrections),
        currency=None,
        line_items=lines,
        subtotal=_optional_money(fields.get("subtotal"), corrections),
        tax_amount=_optional_money(fields.get("tax"), corrections),
        total=_money(fields["total"], corrections),
        payment_terms=None,
        notes=None,
        corrections=corrections,
    )


def _text_line(line: str, corrections: list[str]) -> dict | None:
    stripped = line.strip()
    if stripped.startswith("- "):
        stripped = stripped[2:].strip()
    if not stripped or set(stripped) <= {"-"}:
        return None
    lowered = stripped.lower()
    if lowered.startswith(
        (
            "subtotal",
            "tax",
            "total",
            "grand",
            "amt",
            "notes",
            "terms",
            "payment",
            "invoice",
            "vendor",
            "vndr",
            "from",
            "date",
            "due",
            "inv ",
            "item ",
            "to:",
            "attn",
            "bill ",
            "i n v",
        )
    ):
        return None
    patterns = (
        re.compile(
            r"^(?P<item>.+?)\s+qty:[ \t]*(?P<qty>-?\d+)\s+unit price:[ \t]*\$?(?P<price>[0-9O][0-9O,.]*)$",
            re.I,
        ),
        re.compile(
            r"^(?P<item>.+?)\s+qty\s+(?P<qty>-?\d+)\s+@\s*\$?(?P<price>[0-9O][0-9O,.]*)",
            re.I,
        ),
        re.compile(
            r"^(?P<item>.+?)\s+x(?P<qty>-?\d+)\s+\$?(?P<price>[0-9O][0-9O,.]*)",
            re.I,
        ),
        re.compile(r"^shipping\s*:?\s+\$?(?P<price>[0-9O][0-9O,.]*)\s*$", re.I),
        re.compile(
            r"^(?P<item>.+?)\s+(?P<qty>-?\d+)\s+\$(?P<price>[0-9O][0-9O,.]*)"
            r"(?:\s+\$(?P<amount>[0-9O][0-9O,.]*))?(?:\s+(?P<note>[A-Za-z].*))?$"
        ),
    )
    for pattern in patterns:
        match = pattern.match(stripped)
        if match is None:
            continue
        groups = match.groupdict()
        if "item" not in groups:
            item, note = "Shipping", None
            quantity = 1
        else:
            item, note = _split_note(groups["item"])
            quantity = int(groups["qty"])
        extra_note = groups.get("note")
        item, found = normalize_item_name(item)
        corrections.extend(found)
        amount = groups.get("amount")
        return {
            "item": item,
            "quantity": quantity,
            "unit_price": _money(groups["price"], corrections),
            "amount": _money(amount, corrections) if amount else None,
            "note": extra_note.strip() if extra_note else note,
        }
    return None


def _json_line(raw: dict, corrections: list[str]) -> dict:
    item, found = normalize_item_name(str(raw["item"]))
    corrections.extend(found)
    amount = raw.get("amount")
    return {
        "item": item,
        "quantity": raw["quantity"],
        "unit_price": _money(raw["unit_price"], corrections),
        "amount": _money(amount, corrections) if amount is not None else None,
        "note": raw.get("note"),
    }


def _draft(**fields) -> dict:
    return fields


def _money(value, corrections: list[str]) -> Decimal:
    if isinstance(value, float):
        raise TypeError("money must not be float")
    if isinstance(value, Decimal):
        return value.quantize(CENT)
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value).quantize(CENT)
    amount, found = normalize_amount(str(value).strip())
    corrections.extend(found)
    return amount


def _optional_money(value, corrections: list[str]) -> Decimal | None:
    if value is None or value == "":
        return None
    return _money(value, corrections)


def _optional_date(value, corrections: list[str]) -> date | None:
    if value is None or value == "":
        return None
    parsed, found = parse_date(str(value))
    corrections.extend(found)
    return parsed


def _date_correction(original: str, parsed: date) -> list[str]:
    canonical = parsed.isoformat()
    if original == canonical:
        return []
    return [f"{original} -> {canonical}"]


def _search(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.I | re.M)
    if match is None:
        return None
    return match.group(1).strip()


def _money_search(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.I)
    if match is None:
        return None
    return match.group(1)


def _total_search(text: str) -> str:
    found = None
    for match in re.finditer(
        r"total(?:\s+amount)?\s*:[ \t]*\$?([0-9O][0-9O,.]*)",
        text,
        re.I,
    ):
        prefix = text[max(0, match.start() - 3) : match.start()].lower()
        if prefix == "sub":
            continue
        found = match.group(1)
    if found is None:
        amt = re.search(r"^amt\s*:[ \t]*\$?([0-9O][0-9O,.]*)", text, re.I | re.M)
        if amt:
            found = amt.group(1)
    if found is None:
        raise ValueError("invoice total is missing")
    return found


def _split_note(item: str) -> tuple[str, str | None]:
    match = re.match(r"^(.*?)\s*\((.*)\)\s*$", item.strip())
    if match is None:
        return item.strip(), None
    return match.group(1).strip(), match.group(2).strip()


def _column(header: list[str], *names: str) -> int | None:
    for name in names:
        if name in header:
            return header.index(name)
    return None


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def _summary_cell(row: list[str]) -> tuple[str, str] | None:
    for index, cell in enumerate(row):
        label = cell.strip().lower()
        if not label.endswith(":"):
            continue
        value = _cell(row, index + 1)
        if value:
            return label[:-1].strip(), value
    return None


def _xml_text(node, name: str) -> str:
    if node is None:
        return ""
    child = node.find(name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()
