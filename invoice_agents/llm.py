import json
import math
import os
import sys
import time
from decimal import Decimal
from typing import Any, Protocol

from invoice_agents.models import Finding, Invoice
from invoice_agents.normalize import (
    normalize_amount,
    normalize_date,
    normalize_invoice_number,
    normalize_item_name,
    normalize_vendor_name,
)

MODEL = "grok-4.7"


class Llm(Protocol):
    def extract(self, text: str, *, retry: bool = False) -> dict: ...

    def flag_address(self, address: str | None) -> list[str]: ...

    def summarize(self, invoice: Invoice, findings: list[Finding]) -> str: ...

    def critique(self, summary: str) -> str: ...


class MockLlm:
    """Offline stand-in for Grok. The famous-address list lives only here."""

    def __init__(self) -> None:
        self.summarize_calls = 0
        self.critique_calls = 0

    def extract(self, text: str, *, retry: bool = False) -> dict:
        from invoice_agents.parsers import parse_text

        text = text.split("\n\nProblems to fix:\n", 1)[0]
        return parse_text(text)

    def flag_address(self, address: str | None) -> list[str]:
        if not address:
            return []
        folded = address.casefold()
        if "1600 pennsylvania" in folded or "742 evergreen" in folded:
            return [f"Suspicious vendor address: {address}"]
        return []

    def summarize(self, invoice: Invoice, findings: list[Finding]) -> str:
        self.summarize_calls += 1
        status = _status(findings)
        return f"Invoice {invoice.invoice_number} is {status}."

    def critique(self, summary: str) -> str:
        self.critique_calls += 1
        return f"The summary matches the invoice record. {summary}"


class GrokLlm:
    def __init__(self) -> None:
        api_key = os.environ.get("XAI_API_KEY")
        if not api_key:
            raise SystemExit("XAI_API_KEY is not set.")
        from xai_sdk import Client

        self._client = Client(api_key=api_key, timeout=120)
        self.source_name = "invoice"
        self._step = ""

    def extract(self, text: str, *, retry: bool = False) -> dict:
        verb = "retry extract" if retry else "extract"
        self._step = f"{verb} {self.source_name}"
        raw = self._ask(
            "Extract this invoice as a JSON object with keys invoice_number, "
            "vendor_name, vendor_address, invoice_date, due_date, currency, "
            "line_items, subtotal, tax_amount, and total. line_items is a list of "
            "objects with item, quantity, and unit_price. Write money and dates "
            "as strings. If there is no tax line, return tax_amount \"0.00\". "
            "Return null only if a tax line exists but cannot be read.\n\n" + text,
            json_mode=True,
        )
        draft = _load_json(raw)
        if not isinstance(draft, dict):
            raise ValueError("Grok extract did not return a JSON object")
        return _normalize_draft(draft)

    def flag_address(self, address: str | None) -> list[str]:
        if not address:
            return []
        self._step = "flag_address"
        raw = self._ask(
            "Flag this vendor address if it is a famous fictional or government "
            "address that looks fraudulent. Return JSON "
            '{"warnings": ["one short warning"]}. Return {"warnings": []} if the '
            "address looks ordinary. Do not recommend rejection.\n\n" + address,
            json_mode=True,
        )
        payload = _load_json(raw)
        warnings = payload.get("warnings", []) if isinstance(payload, dict) else []
        if not isinstance(warnings, list):
            return []
        return [str(warning) for warning in warnings]

    def summarize(self, invoice: Invoice, findings: list[Finding]) -> str:
        reasons = "; ".join(finding.reason for finding in findings) or "no findings"
        self._step = "summarize"
        return self._ask(
            "Write one short sentence naming the invoice number and the status "
            f"{_status(findings)}. Do not change the status.\n\n"
            f"Invoice {invoice.invoice_number}. Findings: {reasons}"
        )

    def critique(self, summary: str) -> str:
        self._step = "critique"
        return self._ask(
            "Critique this invoice summary in one short sentence. "
            "Repeat the invoice number and status. Do not change the decision.\n\n"
            + summary
        )

    def _ask(self, prompt: str, *, json_mode: bool = False) -> str:
        api_key = os.environ.get("XAI_API_KEY")
        if not api_key:
            raise SystemExit("XAI_API_KEY is not set.")
        from xai_sdk.chat import user

        step = self._step or f"extract {self.source_name}"
        print(f"[grok] {step}", file=sys.stderr)
        started = time.perf_counter()
        try:
            kwargs: dict[str, Any] = {"model": MODEL}
            if json_mode:
                kwargs["response_format"] = "json_object"
            chat = self._client.chat.create(**kwargs)
            chat.append(user(prompt))
            content = chat.sample().content
        except Exception as exc:
            raise SystemExit(_grok_failure(exc)) from exc
        elapsed = time.perf_counter() - started
        print(f"[grok] done in {elapsed:.1f}s", file=sys.stderr)
        return content


def _grok_failure(exc: Exception) -> str:
    labels = [str(exc)]
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            labels.append(str(code()))
        except Exception:
            pass
    details = getattr(exc, "details", None)
    if callable(details):
        try:
            labels.append(str(details()))
        except Exception:
            pass
    blob = " ".join(labels)
    if "DEADLINE_EXCEEDED" in blob:
        return "Grok call timed out after 120s."
    if "UNAUTHENTICATED" in blob or "PERMISSION_DENIED" in blob:
        return "Grok rejected the API key. Check XAI_API_KEY."
    if "INVALID_ARGUMENT" in blob and "api key" in blob.casefold():
        return "Grok rejected the API key. Check XAI_API_KEY."
    reason = str(exc).strip() or exc.__class__.__name__
    return f"Grok call failed: {reason}"


def _read_grok_amount(value: Any) -> tuple[Decimal | None, list[str], bool]:
    if isinstance(value, str) or value is None:
        amount, found = normalize_amount(value)
        return amount, found, True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, [], False
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None, [], True
    return Decimal(str(value)).quantize(Decimal("0.01")), [], True


def _unreadable(field: str, value: Any) -> str:
    shown = "null" if value is None else repr(value)
    return f"{field} unreadable: {shown}"


def _status(findings: list[Finding]) -> str:
    outcomes = {finding.outcome for finding in findings}
    if "reject" in outcomes:
        return "rejected"
    if "hold" in outcomes:
        return "hold"
    return "approved"


def _load_json(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return json.loads(cleaned)


def _normalize_draft(draft: dict) -> dict:
    corrections: list[str] = []
    number = draft.get("invoice_number")
    if isinstance(number, str):
        canonical, found = normalize_invoice_number(number)
        draft["invoice_number"] = canonical
        corrections.extend(found)
    vendor = draft.get("vendor_name")
    if isinstance(vendor, str):
        canonical, found = normalize_vendor_name(vendor)
        draft["vendor_name"] = canonical
        corrections.extend(found)
    for key in ("invoice_date", "due_date"):
        value = draft.get(key)
        if not isinstance(value, str):
            continue
        try:
            parsed, found = normalize_date(value)
        except ValueError:
            continue
        draft[key] = parsed.isoformat()
        corrections.extend(found)
    items = draft.get("line_items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("item")
            if isinstance(name, str):
                canonical, found = normalize_item_name(name)
                item["item"] = canonical
                corrections.extend(found)
            if "unit_price" not in item:
                continue
            price = item.get("unit_price")
            amount, found, handled = _read_grok_amount(price)
            if not handled:
                continue
            if amount is None:
                item["unit_price"] = None
                corrections.append(_unreadable("unit_price", price))
            else:
                item["unit_price"] = f"{amount:.2f}"
                corrections.extend(found)
    for key in ("subtotal", "tax_amount", "total"):
        if key not in draft:
            continue
        value = draft[key]
        amount, found, handled = _read_grok_amount(value)
        if not handled:
            continue
        if amount is None:
            draft[key] = None
            corrections.append(_unreadable(key, value))
        else:
            draft[key] = amount
            corrections.extend(found)
    existing = draft.get("corrections")
    if not isinstance(existing, list):
        existing = []
    draft["corrections"] = [*existing, *corrections]
    return draft
