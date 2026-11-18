import json
from datetime import datetime, timezone
from pathlib import Path


def append_decision(
    audit_path: str | Path, decision: dict, *, agent: str, run_id: str | None = None
) -> None:
    path = Path(audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
    }
    if run_id:
        record["run_id"] = run_id
    record.update(decision)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def read_decisions(audit_path: str | Path) -> list[dict]:
    path = Path(audit_path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def format_audit_log(audit_path: str | Path) -> str:
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for decision in read_decisions(audit_path):
        run_id = decision.get("run_id") or "unknown"
        if run_id not in grouped:
            grouped[run_id] = []
            order.append(run_id)
        grouped[run_id].append(decision)
    return "\n\n".join(_format_run(run_id, grouped[run_id]) for run_id in order)


def _format_run(run_id: str, decisions: list[dict]) -> str:
    number = next((item.get("invoice_number") for item in decisions if item.get("invoice_number")), "unknown")
    when = _header_time(decisions[0].get("ts", ""))
    lines = [f"=== Run {run_id} | {number} | {when} ==="]
    for decision in decisions:
        lines.append(f"  {decision.get('agent', 'unknown')}: {_event_summary(decision)}")
    return "\n".join(lines)


def _header_time(ts: str) -> str:
    if not ts:
        return "unknown"
    stamp = datetime.fromisoformat(ts).astimezone(timezone.utc)
    return stamp.strftime("%Y-%m-%d %H:%M UTC")


def _event_summary(decision: dict) -> str:
    agent = decision.get("agent")
    if agent == "ingestion" and "attempt" in decision:
        problems = decision.get("problems") or []
        shown = ", ".join(str(problem) for problem in problems) if problems else "none"
        return f"attempt {decision['attempt']}, problems: {shown}"
    if agent == "ingestion":
        reasons = decision.get("reasons") or []
        corrections = decision.get("corrections") or []
        if reasons:
            return "reasons: " + ", ".join(str(reason) for reason in reasons)
        if corrections:
            return "corrections: " + ", ".join(str(correction) for correction in corrections)
        return str(decision.get("status") or "read invoice")
    reason = str(decision.get("reason") or "")
    if agent == "payment" and reason.startswith("not paid:"):
        return "not paid - " + reason.removeprefix("not paid:").strip()
    outcome = decision.get("outcome")
    if outcome and reason:
        return f"{outcome} - {reason}"
    return reason or str(decision.get("status") or "")
