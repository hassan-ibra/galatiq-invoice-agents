import argparse
import json
from datetime import date

from invoice_agents.graph import build_graph
from invoice_agents.models import PipelineState


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--invoice_path", required=True)
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--as-of")
    parser.add_argument("--db", default="data/inventory.db")
    parser.add_argument("--audit", default="logs/audit.jsonl")
    args = parser.parse_args()

    state = PipelineState(
        source_path=args.invoice_path,
        db_path=args.db,
        audit_path=args.audit,
        as_of=None if args.as_of is None else date.fromisoformat(args.as_of),
        auto_approve=args.auto_approve,
        use_llm=args.use_llm,
    )
    result = build_graph().invoke(state)
    if isinstance(result, dict):
        result = PipelineState.model_validate(result)
    print(json.dumps(_payload(result), indent=2))


def _payload(state: PipelineState) -> dict:
    invoice = state.invoice
    return {
        "invoice_number": None if invoice is None else invoice.invoice_number,
        "revision": None if invoice is None else invoice.revision,
        "status": state.status,
        "late": state.late,
        "findings": [finding.model_dump() for finding in state.findings],
        "corrections": [] if invoice is None else invoice.corrections,
        "address_warnings": [] if invoice is None else invoice.address_warnings,
        "summary": state.summary,
        "payment_result": state.payment_result,
    }


if __name__ == "__main__":
    main()
