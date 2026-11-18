import sys
from decimal import Decimal
from pathlib import Path

from invoice_agents.audit import append_decision, read_decisions
from invoice_agents.models import Invoice, PipelineState

CENT = Decimal("0.01")


def mock_payment(vendor: str, amount: Decimal) -> dict[str, str]:
    rendered = f"{amount.quantize(CENT):.2f}"
    print(f"Paid {rendered} to {vendor}", file=sys.stderr)
    return {"status": "success", "vendor": vendor, "amount": rendered}


def pay(state: PipelineState) -> PipelineState:
    invoice = state.invoice
    if invoice is None:
        raise ValueError("payment requires an invoice")

    if state.void_original:
        _log_original_void(state, invoice)

    if state.status == "rejected" or state.status == "hold":
        _log_reasons(state, invoice)
        return state
    if state.status == "voided":
        append_decision(
            state.audit_path,
            {
                "invoice_number": invoice.invoice_number,
                "revision": invoice.revision or "",
                "status": "voided",
                "reason": f"{invoice.invoice_number} is voided and was not paid",
            },
        )
        return state
    if state.status != "approved":
        return state
    if _already_paid(state, invoice):
        append_decision(
            state.audit_path,
            {
                "invoice_number": invoice.invoice_number,
                "revision": invoice.revision or "",
                "status": state.status,
                "reason": (
                    f"invoice {invoice.invoice_number} "
                    f"revision {invoice.revision or 'none'} was already paid"
                ),
            },
        )
        return state

    result = mock_payment(invoice.vendor_name, invoice.total)
    state.payment_result = result
    append_decision(
        state.audit_path,
        {
            "invoice_number": invoice.invoice_number,
            "revision": invoice.revision or "",
            "status": state.status,
            "paid": True,
            "vendor": result["vendor"],
            "amount": result["amount"],
            "reason": f"Paid {result['amount']} to {result['vendor']}",
        },
    )
    return state


def _log_original_void(state: PipelineState, invoice: Invoice) -> None:
    append_decision(
        state.audit_path,
        {
            "invoice_number": invoice.invoice_number,
            "revision": "",
            "status": "voided",
            "reason": (
                f"original {invoice.invoice_number} with no revision is voided "
                f"because revision {invoice.revision} replaces it, "
                "and Acme never pays twice"
            ),
        },
    )


def _log_reasons(state: PipelineState, invoice: Invoice) -> None:
    if not state.findings:
        append_decision(
            state.audit_path,
            {
                "invoice_number": invoice.invoice_number,
                "revision": invoice.revision or "",
                "status": state.status,
                "reason": f"{state.status} with no findings",
            },
        )
        return
    for finding in state.findings:
        append_decision(
            state.audit_path,
            {
                "invoice_number": invoice.invoice_number,
                "revision": invoice.revision or "",
                "status": state.status,
                "rule": finding.rule,
                "outcome": finding.outcome,
                "reason": finding.reason,
            },
        )


def _already_paid(state: PipelineState, invoice: Invoice) -> bool:
    path = Path(state.audit_path)
    if not path.exists():
        return False
    revision = invoice.revision or ""
    for decision in read_decisions(path):
        same_invoice = decision.get("invoice_number") == invoice.invoice_number
        same_revision = (decision.get("revision") or "") == revision
        if same_invoice and same_revision and decision.get("paid") is True:
            return True
    return False
