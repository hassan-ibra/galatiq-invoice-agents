import json
import os
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
    def extract(self, text: str) -> dict: ...

    def flag_address(self, address: str | None) -> list[str]: ...

    def summarize(self, invoice: Invoice, findings: list[Finding]) -> str: ...

    def critique(self, summary: str) -> str: ...


class MockLlm:
    """Offline stand-in for Grok. The famous-address list lives only here."""

    def __init__(self) -> None:
        self.summarize_calls = 0
        self.critique_calls = 0

    def extract(self, text: str) -> dict:
        from invoice_agents.parsers import parse_text

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
            raise SystemExit("XAI_API_KEY is not set. Refusing to call Grok.")
        from xai_sdk import Client

        self._client = Client(api_key=api_key)

    def extract(self, text: str) -> dict:
        raw = self._ask(
            "Extract this invoice as a JSON object with keys invoice_number, "
            "vendor_name, vendor_address, invoice_date, due_date, currency, "
            "line_items, subtotal, tax_amount, and total. line_items is a list of "
            "objects with item, quantity, and unit_price. Write money and dates "
            "as strings.\n\n" + text,
            json_mode=True,
        )
        draft = _load_json(raw)
        if not isinstance(draft, dict):
            raise ValueError("Grok extract did not return a JSON object")
        return _normalize_draft(draft)

    def flag_address(self, address: str | None) -> list[str]:
        if not address:
            return []
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
        return self._ask(
            "Write one short sentence naming the invoice number and the status "
            f"{_status(findings)}. Do not change the status.\n\n"
            f"Invoice {invoice.invoice_number}. Findings: {reasons}"
        )

    def critique(self, summary: str) -> str:
        return self._ask(
            "Critique this invoice summary in one short sentence. "
            "Repeat the invoice number and status. Do not change the decision.\n\n"
            + summary
        )

    def _ask(self, prompt: str, *, json_mode: bool = False) -> str:
        from xai_sdk.chat import user

        kwargs: dict[str, Any] = {"model": MODEL}
        if json_mode:
            kwargs["response_format"] = "json_object"
        chat = self._client.chat.create(**kwargs)
        chat.append(user(prompt))
        return chat.sample().content


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
            price = item.get("unit_price")
            if isinstance(price, str):
                amount, found = normalize_amount(price)
                item["unit_price"] = f"{amount:.2f}"
                corrections.extend(found)
    existing = draft.get("corrections")
    if not isinstance(existing, list):
        existing = []
    draft["corrections"] = [*existing, *corrections]
    return draft
