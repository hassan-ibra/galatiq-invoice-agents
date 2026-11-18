from decimal import Decimal

from invoice_agents.audit import append_decision
from invoice_agents.inventory import get_item
from invoice_agents.models import Finding, Invoice, LineItem, PipelineState

CENT = Decimal("0.01")
PRICE_LIMIT = Decimal("1.10")


def validate(state: PipelineState) -> PipelineState:
    invoice = state.invoice
    if invoice is None:
        raise ValueError("validation requires an invoice")

    findings: list[Finding] = []
    findings.extend(_unreadable_money(invoice))
    findings.extend(_check_data_integrity(invoice))
    findings.extend(_check_total(invoice))
    non_usd = invoice.currency != "USD"
    if non_usd:
        findings.append(
            Finding(
                rule=3,
                outcome="hold",
                reason=f"currency {invoice.currency} is not USD",
            )
        )
    findings.extend(_check_known_items(invoice, state.db_path))
    findings.extend(_check_stock(invoice, state.db_path))
    if not non_usd:
        findings.extend(_check_prices(invoice, state.db_path))

    state.findings.extend(findings)
    state.apply_findings()
    _log(state, invoice)
    return state


def _check_data_integrity(invoice: Invoice) -> list[Finding]:
    findings: list[Finding] = []
    for line in invoice.line_items:
        if line.quantity < 1:
            findings.append(
                Finding(
                    rule=6,
                    outcome="reject",
                    reason=f"{line.item} quantity {line.quantity} is not a positive integer",
                )
            )
        if line.unit_price is None:
            continue
        expected_amount = _cents(line.unit_price * line.quantity)
        if line.amount is not None and _cents(line.amount) != expected_amount:
            findings.append(
                Finding(
                    rule=6,
                    outcome="reject",
                    reason=(
                        f"{line.item} line amount {_cents(line.amount):.2f} "
                        f"does not equal {expected_amount:.2f}"
                    ),
                )
            )
    if invoice.subtotal is not None:
        amounts = [
            _line_amount(line) for line in invoice.line_items if not _is_shipping(line)
        ]
        if any(amount is None for amount in amounts):
            return findings
        computed = _cents(sum(amounts, Decimal("0")))
        stated = _cents(invoice.subtotal)
        if stated != computed:
            findings.append(
                Finding(
                    rule=6,
                    outcome="reject",
                    reason=f"stated subtotal {stated:.2f} does not equal line sum {computed:.2f}",
                )
            )
    return findings


def _unreadable_money(invoice: Invoice) -> list[Finding]:
    return [
        Finding(rule=6, outcome="hold", reason=note)
        for note in invoice.corrections
        if "unreadable:" in note
    ]


def _check_total(invoice: Invoice) -> list[Finding]:
    if invoice.total is None or invoice.tax_amount is None:
        return []
    if any(line.unit_price is None and line.amount is None for line in invoice.line_items):
        return []
    expected = _cents(
        sum((_line_amount(line) for line in invoice.line_items), Decimal("0"))
        + invoice.tax_amount
    )
    stated = _cents(invoice.total)
    if stated == expected:
        return []
    difference = _cents(stated - expected)
    return [
        Finding(
            rule=4,
            outcome="reject",
            reason=(
                f"stated total {stated:.2f} minus expected {expected:.2f} "
                f"is {difference:.2f}"
            ),
        )
    ]


def _check_known_items(invoice: Invoice, db_path: str) -> list[Finding]:
    findings: list[Finding] = []
    for line in invoice.line_items:
        if _is_shipping(line):
            continue
        record = get_item(db_path, line.item)
        if record is None:
            findings.append(
                Finding(
                    rule=5,
                    outcome="reject",
                    reason=f"{line.item} is not in inventory",
                )
            )
        elif record.stock <= 0:
            findings.append(
                Finding(
                    rule=5,
                    outcome="reject",
                    reason=f"{line.item} has zero stock",
                )
            )
    return findings


def _check_stock(invoice: Invoice, db_path: str) -> list[Finding]:
    quantities: dict[str, int] = {}
    for line in invoice.line_items:
        if _is_shipping(line) or line.quantity < 1:
            continue
        record = get_item(db_path, line.item)
        if record is None or record.stock <= 0:
            continue
        quantities[line.item] = quantities.get(line.item, 0) + line.quantity

    findings: list[Finding] = []
    for item, quantity in quantities.items():
        record = get_item(db_path, item)
        if record is not None and quantity > record.stock:
            findings.append(
                Finding(
                    rule=1,
                    outcome="hold",
                    reason=f"{item} quantity {quantity} exceeds stock {record.stock}",
                )
            )
    return findings


def _check_prices(invoice: Invoice, db_path: str) -> list[Finding]:
    findings: list[Finding] = []
    for line in invoice.line_items:
        if _is_shipping(line):
            continue
        record = get_item(db_path, line.item)
        if record is None or record.stock <= 0 or line.unit_price is None:
            continue
        if line.unit_price > record.unit_price * PRICE_LIMIT:
            findings.append(
                Finding(
                    rule=9,
                    outcome="hold",
                    reason=(
                        f"{line.item} unit price {line.unit_price:.2f} "
                        f"is above catalog {record.unit_price:.2f}"
                    ),
                )
            )
    return findings


def _log(state: PipelineState, invoice: Invoice) -> None:
    if not state.findings:
        append_decision(
            state.audit_path,
            {
                "invoice_number": invoice.invoice_number,
                "status": state.status,
                "reason": "no findings",
            },
        )
        return
    for finding in state.findings:
        append_decision(
            state.audit_path,
            {
                "invoice_number": invoice.invoice_number,
                "status": state.status,
                "rule": finding.rule,
                "outcome": finding.outcome,
                "reason": finding.reason,
            },
        )


def _is_shipping(line: LineItem) -> bool:
    return line.item.casefold() == "shipping"


def _line_amount(line: LineItem) -> Decimal | None:
    if line.amount is not None:
        return _cents(line.amount)
    if line.unit_price is None:
        return None
    return _cents(line.unit_price * line.quantity)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT)
