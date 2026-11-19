import copy
from decimal import Decimal
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
    if isinstance(llm, GrokLlm):
        llm.source_name = requested.name
    cache: dict[Path, tuple[dict, Invoice | None, list[str], int]] = {}
    try:
        draft, invoice, problems, attempts = _settle(
            requested, llm, cache, state.audit_path, state.run_id
        )
    except (ValidationError, ValueError, OSError) as exc:
        state.status = "rejected"
        state.summary = str(exc)
        _log(state)
        return state
    if invoice is None:
        state.attempts = attempts
        state.extraction_critique = list(problems)
        state.status = "rejected"
        state.summary = "; ".join(problems) or "invoice could not be read"
        _log(state)
        return state

    copies = [(requested, draft)]
    for path in sorted(requested.parent.iterdir()):
        if path.resolve() == requested.resolve() or path.suffix.lower() not in READABLE:
            continue
        if not _filename_has_invoice_number(path, invoice.invoice_number):
            continue
        try:
            other, _other_invoice, _other_problems, _other_attempts = _settle(
                path, llm, cache, state.audit_path, state.run_id
            )
        except (ValidationError, ValueError, OSError, SystemExit):
            continue
        if other.get("invoice_number") == invoice.invoice_number:
            copies.append((path, other))

    if len(copies) == 1:
        merged_invoice = invoice
        findings = _problem_findings(problems)
        state.attempts = attempts
        state.extraction_critique = list(problems)
    elif _has_revision(copies) and _has_original(copies):
        chosen_path, _chosen = _pick_revised(copies)
        _chosen_draft, chosen_invoice, chosen_problems, chosen_attempts = cache[chosen_path.resolve()]
        state.attempts = chosen_attempts
        state.extraction_critique = list(chosen_problems)
        if chosen_invoice is None:
            state.status = "rejected"
            state.summary = "; ".join(chosen_problems) or "invoice could not be read"
            _log(state)
            return state
        chosen_invoice.source_files = [str(chosen_path)]
        chosen_invoice.address_warnings = llm.flag_address(chosen_invoice.vendor_address)
        state.invoice = chosen_invoice
        state.void_original = True
        state.findings.extend(_problem_findings(chosen_problems))
        if state.findings:
            state.apply_findings()
        _log(state)
        return state
    else:
        merged, findings = _merge(copies)
        merged_invoice, merged_problems = _assess(merged)
        state.attempts = attempts
        state.extraction_critique = list(merged_problems)
        if merged_invoice is None:
            state.status = "rejected"
            state.summary = "; ".join(merged_problems) or "invoice could not be read"
            _log(state)
            return state
        findings.extend(_problem_findings(merged_problems))
    merged_invoice.source_files = [str(path) for path, _draft in copies]
    merged_invoice.address_warnings = llm.flag_address(merged_invoice.vendor_address)
    state.invoice = merged_invoice
    state.findings.extend(findings)
    if findings:
        state.apply_findings()
    _log(state)
    return state


def _filename_has_invoice_number(path: Path, invoice_number: str) -> bool:
    if not invoice_number:
        return False
    name = path.name.casefold()
    number = invoice_number.casefold()
    if number in name:
        return True
    digits = "".join(char for char in invoice_number if char.isdigit())
    return bool(digits) and digits in name


def _load_draft(path: Path, llm) -> dict:
    if isinstance(llm, GrokLlm):
        llm.source_name = path.name
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


def _settle(
    path: Path,
    llm,
    cache: dict[Path, tuple[dict, Invoice | None, list[str], int]],
    audit_path: str,
    run_id: str,
) -> tuple[dict, Invoice | None, list[str], int]:
    key = path.resolve()
    if key in cache:
        return cache[key]
    draft = _load_draft(path, llm)
    invoice, problems = _assess(draft)
    _log_attempt(audit_path, path, 1, draft, problems, run_id)
    attempts = 1
    if problems and path.suffix.lower() in {".txt", ".pdf"}:
        if isinstance(llm, GrokLlm):
            llm.source_name = path.name
        raw = pdf_text(path) if path.suffix.lower() == ".pdf" else path.read_text(encoding="utf-8")
        prompt = raw + "\n\nProblems to fix:\n" + "\n".join(problems)
        try:
            retried = llm.extract(prompt, retry=True)
        except (ValidationError, ValueError, OSError, SystemExit) as exc:
            retried = None
            failure = str(exc) or "extraction failed"
        else:
            failure = ""
        attempts = 2
        if retried is None:
            _log_attempt(audit_path, path, 2, {}, [failure], run_id)
        else:
            invoice2, problems2 = _assess(retried)
            _log_attempt(audit_path, path, 2, retried, problems2, run_id)
            if _prefer_second(invoice, problems, invoice2, problems2):
                draft, invoice, problems = retried, invoice2, problems2
    cache[key] = (draft, invoice, problems, attempts)
    return cache[key]


def _log_attempt(
    audit_path: str,
    path: Path,
    attempt: int,
    draft: dict,
    problems: list[str],
    run_id: str,
) -> None:
    append_decision(
        audit_path,
        {
            "attempt": attempt,
            "source": path.name,
            "invoice_number": draft.get("invoice_number"),
            "problems": problems,
        },
        agent="ingestion",
        run_id=run_id,
    )


def _prefer_second(
    first_invoice: Invoice | None,
    first_problems: list[str],
    second_invoice: Invoice | None,
    second_problems: list[str],
) -> bool:
    if second_invoice is None:
        return False
    if len(second_problems) < len(first_problems):
        return True
    return first_invoice is None and len(second_problems) == len(first_problems)


def _assess(draft: dict) -> tuple[Invoice | None, list[str]]:
    try:
        invoice = _invoice_from_draft(draft)
    except ValidationError as exc:
        return None, _schema_problems(exc)
    return invoice, _invoice_problems(invoice)


def _schema_problems(exc: ValidationError) -> list[str]:
    problems = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"])
        problems.append(f"{loc}: {err['msg']}" if loc else str(err["msg"]))
    return problems or [str(exc)]


def _problem_findings(problems: list[str]) -> list[Finding]:
    return [Finding(rule=6, outcome="hold", reason=problem) for problem in problems]


def _invoice_problems(invoice: Invoice) -> list[str]:
    problems: list[str] = []
    if not invoice.invoice_number.strip():
        problems.append("invoice number is missing")
    if not invoice.vendor_name.strip():
        problems.append("vendor name is empty")
    if not invoice.line_items:
        problems.append("invoice has no line items")
    if invoice.total is None:
        problems.append("invoice total is missing")
    subtotal = _subtotal_problem(invoice)
    if subtotal:
        problems.append(subtotal)
    total = _total_problem(invoice)
    if total:
        problems.append(total)
    return problems


def _subtotal_problem(invoice: Invoice) -> str | None:
    if invoice.subtotal is None:
        return None
    amounts = [
        _line_amount(line) for line in invoice.line_items if line.item.casefold() != "shipping"
    ]
    if any(amount is None for amount in amounts):
        return None
    computed = _cents(sum(amounts, Decimal("0")))
    stated = _cents(invoice.subtotal)
    if stated == computed:
        return None
    return f"stated subtotal {stated:.2f} does not equal line sum {computed:.2f}"


def _total_problem(invoice: Invoice) -> str | None:
    if invoice.total is None or invoice.tax_amount is None:
        return None
    amounts = [_line_amount(line) for line in invoice.line_items]
    if any(amount is None for amount in amounts):
        return None
    shipping = _cents(
        sum(
            (
                amount
                for line, amount in zip(invoice.line_items, amounts)
                if line.item.casefold() == "shipping" and amount is not None
            ),
            Decimal("0"),
        )
    )
    fee = invoice.shipping_amount if invoice.shipping_amount is not None else Decimal("0")
    if invoice.subtotal is None:
        expected = _cents(sum(amounts, Decimal("0")) + invoice.tax_amount + fee)
    else:
        expected = _cents(invoice.subtotal + invoice.tax_amount + shipping + fee)
    stated = _cents(invoice.total)
    if stated == expected:
        return None
    return f"subtotal + tax {expected:.2f} does not equal total {stated:.2f}"


def _line_amount(line: LineItem) -> Decimal | None:
    if line.amount is not None:
        return _cents(line.amount)
    if line.unit_price is None:
        return None
    return _cents(line.unit_price * line.quantity)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _invoice_from_draft(draft: dict) -> Invoice:
    lines = []
    for raw in draft.get("line_items") or []:
        payload = {
            "item": raw["item"],
            "quantity": _quantity(raw["quantity"]),
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
    if "subtotal" in draft:
        data["subtotal"] = draft["subtotal"]
    if "tax_amount" in draft:
        data["tax_amount"] = draft["tax_amount"]
    if "shipping_amount" in draft:
        data["shipping_amount"] = draft["shipping_amount"]
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
            "shipping_amount",
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


def _quantity(value):
    from decimal import Decimal, InvalidOperation

    if isinstance(value, bool):
        raise ValueError(f"quantity {value} is not a whole number")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, float):
        number = Decimal(str(value))
    else:
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            raise ValueError(f"quantity {value} is not a whole number") from None
    if number != number.to_integral_value():
        return number
    return int(number)


def _signature(lines: list[dict]) -> list[tuple]:
    from decimal import Decimal

    return [
        (
            line["item"],
            _quantity(line["quantity"]),
            Decimal(line["unit_price"]).quantize(Decimal("0.01")),
        )
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
            "invoice_number": None if invoice is None else invoice.invoice_number,
            "status": state.status,
            "void_original": state.void_original,
            "reasons": reasons,
            "corrections": [] if invoice is None else invoice.corrections,
        },
        agent="ingestion",
        run_id=state.run_id,
    )
