import copy
from pathlib import Path

from pydantic import ValidationError

from invoice_agents.audit import append_decision
from invoice_agents.llm import GrokLlm, MockLlm
from invoice_agents.models import Finding, Invoice, LineItem, PipelineState
from invoice_agents.parsers import parse_csv, parse_json, parse_xml, pdf_text

READABLE = {".json", ".csv", ".xml", ".txt", ".pdf"}


def ingest(state: PipelineState) -> PipelineState:
    llm = GrokLlm() if state.use_llm else MockLlm()
    requested = Path(state.source_path)
    try:
        draft = _load_draft(requested, llm)
        invoice = _build_invoice(draft, requested, llm)
    except (ValidationError, ValueError) as exc:
        state.status = "rejected"
        state.summary = str(exc)
        _log(state)
        return state

    copies = [(requested, draft)]
    for path in sorted(requested.parent.iterdir()):
        if path.resolve() == requested.resolve() or path.suffix.lower() not in READABLE:
            continue
        try:
            other = _load_draft(path, llm)
        except (ValidationError, ValueError, OSError):
            continue
        if other.get("invoice_number") == invoice.invoice_number:
            copies.append((path, other))

    if _has_revision(copies) and _has_original(copies):
        chosen_path, chosen = _pick_revised(copies)
        try:
            chosen_invoice = _build_invoice(chosen, chosen_path, llm)
        except (ValidationError, ValueError) as exc:
            state.status = "rejected"
            state.summary = str(exc)
            _log(state)
            return state
        chosen_invoice.source_files = [str(chosen_path)]
        chosen_invoice.address_warnings = llm.flag_address(chosen_invoice.vendor_address)
        state.invoice = chosen_invoice
        state.void_original = True
        _log(state)
        return state

    merged, findings = _merge(copies)
    try:
        merged_invoice = _build_invoice(merged, requested, llm)
    except (ValidationError, ValueError) as exc:
        state.status = "rejected"
        state.summary = str(exc)
        _log(state)
        return state
    merged_invoice.source_files = [str(path) for path, _draft in copies]
    merged_invoice.address_warnings = llm.flag_address(merged_invoice.vendor_address)
    state.invoice = merged_invoice
    state.findings.extend(findings)
    if findings:
        state.apply_findings()
    _log(state)
    return state


def _load_draft(path: Path, llm) -> dict:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return llm.extract(pdf_text(path))
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        return parse_json(text)
    if suffix == ".csv":
        return parse_csv(text)
    if suffix == ".xml":
        return parse_xml(text)
    if suffix == ".txt":
        return llm.extract(text)
    raise ValueError(f"unsupported invoice file: {path.name}")


def _build_invoice(draft: dict, path: Path, llm) -> Invoice:
    try:
        return _invoice_from_draft(draft)
    except ValidationError as exc:
        if path.suffix.lower() not in {".txt", ".pdf"}:
            raise
        raw = pdf_text(path) if path.suffix.lower() == ".pdf" else path.read_text(encoding="utf-8")
        retried = llm.extract(f"{raw}\n\nThe draft failed validation:\n{exc}")
        return _invoice_from_draft(retried)


def _invoice_from_draft(draft: dict) -> Invoice:
    lines = []
    for raw in draft.get("line_items") or []:
        payload = {
            "item": raw["item"],
            "quantity": int(raw["quantity"]),
            "unit_price": raw["unit_price"],
        }
        if raw.get("amount") is not None:
            payload["amount"] = raw["amount"]
        if raw.get("note"):
            payload["note"] = raw["note"]
        lines.append(LineItem(**payload))
    data = {
        "invoice_number": draft["invoice_number"],
        "vendor_name": draft.get("vendor_name") or "",
        "line_items": lines,
        "total": draft["total"],
        "corrections": _unique(list(draft.get("corrections") or [])),
    }
    for key in (
        "revision",
        "vendor_address",
        "invoice_date",
        "due_date",
        "currency",
        "payment_terms",
        "notes",
    ):
        if draft.get(key):
            data[key] = draft[key]
    if draft.get("subtotal") is not None:
        data["subtotal"] = draft["subtotal"]
    if draft.get("tax_amount") is not None:
        data["tax_amount"] = draft["tax_amount"]
    return Invoice(**data)


def _merge(copies: list[tuple[Path, dict]]) -> tuple[dict, list[Finding]]:
    base = copy.deepcopy(copies[0][1])
    findings: list[Finding] = []
    disagreed: set[str] = set()
    for _path, other in copies[1:]:
        for field in (
            "vendor_name",
            "vendor_address",
            "invoice_date",
            "due_date",
            "currency",
            "subtotal",
            "tax_amount",
            "total",
            "payment_terms",
            "notes",
        ):
            base_value = base.get(field)
            other_value = other.get(field)
            if _blank(base_value) and not _blank(other_value):
                base[field] = other_value
            elif (
                field not in disagreed
                and not _blank(base_value)
                and not _blank(other_value)
                and base_value != other_value
            ):
                disagreed.add(field)
                findings.append(
                    Finding(
                        rule=8,
                        outcome="hold",
                        reason=f"copies disagree on {field}",
                    )
                )
        base_lines = _signature(base.get("line_items") or [])
        other_lines = _signature(other.get("line_items") or [])
        if not base_lines and other_lines:
            base["line_items"] = other["line_items"]
        elif (
            "line_items" not in disagreed
            and base_lines
            and other_lines
            and base_lines != other_lines
        ):
            disagreed.add("line_items")
            findings.append(
                Finding(
                    rule=8,
                    outcome="hold",
                    reason="copies disagree on line_items",
                )
            )
        for correction in other.get("corrections") or []:
            if correction not in base["corrections"]:
                base["corrections"].append(correction)
    return base, findings


def _unique(corrections: list[str]) -> list[str]:
    unique: list[str] = []
    for correction in corrections:
        if correction not in unique:
            unique.append(correction)
    return unique


def _signature(lines: list[dict]) -> list[tuple]:
    from decimal import Decimal

    return [
        (line["item"], int(line["quantity"]), Decimal(line["unit_price"]).quantize(Decimal("0.01")))
        for line in lines
    ]


def _blank(value) -> bool:
    return value is None or value == "" or value == []


def _is_revised(path: Path, draft: dict) -> bool:
    if draft.get("revision"):
        return True
    if "_revised" in path.name.lower():
        return True
    return "revised" in str(draft.get("notes") or "").casefold()


def _has_revision(copies: list[tuple[Path, dict]]) -> bool:
    return any(_is_revised(path, draft) for path, draft in copies)


def _has_original(copies: list[tuple[Path, dict]]) -> bool:
    return any(not _is_revised(path, draft) for path, draft in copies)


def _pick_revised(copies: list[tuple[Path, dict]]) -> tuple[Path, dict]:
    revised = [copy for copy in copies if _is_revised(copy[0], copy[1])]
    for path, draft in revised:
        if draft.get("revision"):
            return path, draft
    for path, draft in revised:
        if "_revised" in path.name.lower():
            return path, draft
    return revised[0]


def _log(state: PipelineState) -> None:
    invoice = state.invoice
    reasons = [finding.reason for finding in state.findings]
    if not reasons and state.summary:
        reasons = [state.summary]
    append_decision(
        state.audit_path,
        {
            "agent": "ingestion",
            "invoice_number": None if invoice is None else invoice.invoice_number,
            "status": state.status,
            "void_original": state.void_original,
            "reasons": reasons,
            "corrections": [] if invoice is None else invoice.corrections,
        },
    )
