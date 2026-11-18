from decimal import Decimal

from invoice_agents.audit import append_decision
from invoice_agents.llm import GrokLlm, MockLlm
from invoice_agents.models import Finding, Invoice, PipelineState

GATE = Decimal("10000")
CENT = Decimal("0.01")


def approve(state: PipelineState) -> PipelineState:
    invoice = state.invoice
    if invoice is None:
        raise ValueError("approval requires an invoice")

    llm = GrokLlm() if state.use_llm else MockLlm()

    new_findings: list[Finding] = []
    if invoice.address_warnings:
        new_findings.append(
            Finding(
                rule=10,
                outcome="hold",
                reason=(
                    "vendor address flagged for human review: "
                    + "; ".join(invoice.address_warnings)
                ),
            )
        )

    reference = state.as_of if state.as_of is not None else invoice.invoice_date
    if (
        invoice.due_date is not None
        and reference is not None
        and invoice.due_date < reference
    ):
        state.late = True
        new_findings.append(
            Finding(
                rule=11,
                outcome="info",
                reason=(
                    f"due date {invoice.due_date.isoformat()} is before "
                    f"{reference.isoformat()}"
                ),
            )
        )
    else:
        state.late = False

    skipped_gate = False
    if invoice.currency == "USD" and invoice.total > GATE:
        amount = _money(invoice.total)
        if state.status == "approved":
            if state.auto_approve:
                skipped_gate = True
            else:
                new_findings.append(
                    Finding(
                        rule=12,
                        outcome="hold",
                        reason=(
                            f"total {amount} is over 10000.00 and a person must approve it"
                        ),
                    )
                )
        else:
            new_findings.append(
                Finding(
                    rule=12,
                    outcome="info",
                    reason=f"total {amount} is over 10000.00",
                )
            )

    state.findings.extend(new_findings)
    state.apply_findings()
    state.summary = llm.summarize(invoice, state.findings)
    state.critique = llm.critique(state.summary)
    _log(state, invoice, new_findings, skipped_gate)
    return state


def _log(
    state: PipelineState,
    invoice: Invoice,
    new_findings: list[Finding],
    skipped_gate: bool,
) -> None:
    if skipped_gate:
        append_decision(
            state.audit_path,
            {
                "invoice_number": invoice.invoice_number,
                "status": state.status,
                "rule": 12,
                "reason": f"human gate skipped for total {_money(invoice.total)}",
            },
        )
    if not new_findings and not skipped_gate:
        append_decision(
            state.audit_path,
            {
                "invoice_number": invoice.invoice_number,
                "status": state.status,
                "reason": state.summary,
            },
        )
        return
    for finding in new_findings:
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


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT):.2f}"
