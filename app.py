import html
import json
import os
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

import streamlit as st

from invoice_agents.audit import format_audit_log, read_decisions
from invoice_agents.graph import build_graph
from invoice_agents.models import PipelineState
from main import _payload

GREEN = "#009a6c"
AMBER = "#e6a817"
RED = "#e23b3b"
UI_AUDIT = Path("logs/ui.jsonl")
UI_DB = Path("data/inventory.db")
GROK_KEY_MESSAGE = (
    "XAI_API_KEY is not set. Add it to the environment, or turn off "
    "Use real Grok to run with the offline mock."
)


def grok_key_error(use_grok: bool, api_key: str | None) -> str | None:
    if use_grok and not (api_key or "").strip():
        return GROK_KEY_MESSAGE
    return None


def status_badge(status: str, paid: bool) -> tuple[str, str]:
    if paid:
        return "paid", GREEN
    if status == "approved":
        return "approved", GREEN
    if status == "hold":
        return "hold", AMBER
    if status == "rejected":
        return "rejected", RED
    return status, "#202020"


def save_upload(filename: str, data: bytes, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(filename).name
    path.write_bytes(data)
    return path


def run_events(audit_path: Path, run_id: str) -> str:
    if not audit_path.is_file():
        return ""
    kept = [
        decision
        for decision in read_decisions(audit_path)
        if decision.get("run_id") == run_id
    ]
    if not kept:
        return ""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as handle:
        for decision in kept:
            handle.write(json.dumps(decision) + "\n")
        temp_path = handle.name
    try:
        return format_audit_log(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def run_invoice(path: Path, *, use_llm: bool, audit_path: Path, db_path: Path) -> dict:
    state = PipelineState(
        source_path=str(path),
        db_path=str(db_path),
        audit_path=str(audit_path),
        use_llm=use_llm,
    )
    result = build_graph().invoke(state)
    if isinstance(result, dict):
        result = PipelineState.model_validate(result)
    invoice = result.invoice
    payload = _payload(result)
    paid = bool(payload["payment_result"] and payload["payment_result"].get("status") == "success")
    total = None
    if invoice is not None and invoice.total is not None:
        total = f"{invoice.total:.2f}"
    label, color = status_badge(result.status, paid)
    return {
        "payload": payload,
        "vendor": None if invoice is None else invoice.vendor_name,
        "total": total,
        "badge": label,
        "color": color,
        "run_id": result.run_id,
        "events": run_events(audit_path, result.run_id),
    }


_SEVERITY = {"reject": 0, "hold": 1, "info": 2}
_SECTION_TITLES = {
    "reject": "Why rejected",
    "hold": "Also needs attention",
    "info": "Notes",
}


def _sentence(reason: str) -> str:
    text = reason.strip()
    if not text:
        return text
    if text[-1] not in ".!?":
        text += "."
    return text[0].upper() + text[1:]


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _hold_total_pair(reason: str) -> tuple[Decimal, Decimal] | None:
    prefix = "subtotal + tax "
    middle = " does not equal total "
    if not reason.startswith(prefix) or middle not in reason:
        return None
    expected_text, stated_text = reason[len(prefix) :].split(middle, 1)
    expected = _decimal(expected_text)
    stated = _decimal(stated_text)
    if expected is None or stated is None:
        return None
    return expected, stated


def _reject_total_pair(reason: str) -> tuple[Decimal, Decimal] | None:
    prefix = "stated total "
    middle = " minus expected "
    tail = " is "
    if middle not in reason or tail not in reason or not reason.startswith(prefix):
        return None
    stated_text, rest = reason[len(prefix) :].split(middle, 1)
    expected_text, _difference = rest.split(tail, 1)
    expected = _decimal(expected_text)
    stated = _decimal(stated_text)
    if expected is None or stated is None:
        return None
    return expected, stated


def _without_duplicate_total_holds(findings: list[dict]) -> list[dict]:
    rejected: set[tuple[Decimal, Decimal]] = set()
    for finding in findings:
        if finding["outcome"] != "reject":
            continue
        pair = _reject_total_pair(finding["reason"])
        if pair is not None:
            rejected.add(pair)
    if not rejected:
        return list(findings)
    kept = []
    for finding in findings:
        if finding["outcome"] == "hold":
            pair = _hold_total_pair(finding["reason"])
            if pair is not None and pair in rejected:
                continue
        kept.append(finding)
    return kept


_ITEM_PROBLEMS = {
    "is not in inventory": "are not in inventory",
    "has zero stock": "have zero stock",
}


def _item_problem(reason: str) -> str | None:
    for suffix in _ITEM_PROBLEMS:
        if reason.endswith(" " + suffix):
            return suffix
    return None


def _summary_clauses(reasons: list[str]) -> list[str]:
    groups: list[tuple[str | None, list[str]]] = []
    index_by_key: dict[str, int] = {}
    for reason in reasons:
        key = _item_problem(reason)
        if key is None:
            groups.append((None, [reason]))
            continue
        if key not in index_by_key:
            index_by_key[key] = len(groups)
            groups.append((key, []))
        groups[index_by_key[key]][1].append(reason)
    clauses = []
    for key, grouped in groups:
        if key is not None and len(grouped) > 1:
            clauses.append(f"{len(grouped)} items {_ITEM_PROBLEMS[key]}")
        else:
            clauses.append(grouped[0])
    return clauses


def _problem_summary(label: str, reasons: list[str]) -> str:
    clauses = _summary_clauses(reasons)
    if len(clauses) == 1:
        detail = clauses[0]
    else:
        detail = ", ".join(clauses[:-1]) + ", and " + clauses[-1]
    return _sentence(f"{label} for {len(reasons)} problems: {detail}")


def group_findings(findings: list[dict]) -> dict:
    kept = _without_duplicate_total_holds(findings)
    if not kept:
        return {"headline": None, "sections": []}

    lead = min(kept, key=lambda finding: _SEVERITY[finding["outcome"]])
    rejects = [finding["reason"] for finding in kept if finding["outcome"] == "reject"]
    holds = [finding["reason"] for finding in kept if finding["outcome"] == "hold"]
    if len(rejects) > 1:
        headline = _problem_summary("Rejected", rejects)
    elif not rejects and len(holds) > 1:
        headline = _problem_summary("Held", holds)
    else:
        headline = _sentence(lead["reason"])

    remaining: dict[str, list[str]] = {"reject": [], "hold": [], "info": []}
    for finding in kept:
        if finding is lead and lead["outcome"] == "info":
            continue
        remaining[finding["outcome"]].append(finding["reason"])

    sections = []
    for outcome in ("reject", "hold", "info"):
        reasons = remaining[outcome]
        if not reasons:
            continue
        sections.append({"title": _SECTION_TITLES[outcome], "reasons": reasons})
    return {"headline": headline, "sections": sections}


def _badge_html(label: str, color: str) -> str:
    return (
        "<div style='display:inline-block;padding:14px 22px;border-radius:10px;"
        f"background:{color};color:#ffffff;font-size:28px;font-weight:700;"
        f"letter-spacing:0.04em;text-transform:uppercase'>{label}</div>"
    )


def run_log_html(events: str) -> str:
    text = html.escape(events or "No events.")
    return (
        "<div style='white-space:pre-wrap;overflow-wrap:anywhere;"
        "font-family:monospace'>"
        f"{text}</div>"
    )


def show_result(view: dict) -> None:
    st.markdown(_badge_html(view["badge"], view["color"]), unsafe_allow_html=True)
    payload = view["payload"]
    grouped = group_findings(payload["findings"])
    if grouped["headline"]:
        st.markdown(f"**{grouped['headline']}**")
    st.subheader(payload["invoice_number"] or "No invoice number")
    vendor = view["vendor"] or "—"
    total = view["total"] or "—"
    st.write(f"Vendor: {vendor}")
    st.write(f"Total: {total}")
    if grouped["sections"]:
        for section in grouped["sections"]:
            st.markdown(f"**{section['title']}**")
            for reason in section["reasons"]:
                st.write(reason)
    elif not grouped["headline"]:
        st.markdown("**Findings**")
        st.write("No findings.")
    st.markdown("**Run**")
    st.markdown(run_log_html(view["events"]), unsafe_allow_html=True)
    with st.expander("Raw output"):
        st.code(json.dumps(payload, indent=2), language="json")


def render() -> None:
    st.set_page_config(page_title="Acme Invoice Agents", layout="centered")
    st.title("Acme Invoice Agents")
    uploaded = st.file_uploader(
        "Invoice file",
        type=["txt", "pdf", "json", "csv", "xml"],
    )
    use_grok = st.toggle("Use real Grok", value=False)
    if uploaded is None:
        return
    message = grok_key_error(use_grok, os.environ.get("XAI_API_KEY"))
    if message:
        st.error(message)
        return
    key = (uploaded.name, uploaded.size, use_grok)
    if st.session_state.get("invoice_key") != key:
        folder = Path(tempfile.mkdtemp(prefix="acme-invoice-"))
        saved = save_upload(uploaded.name, uploaded.getvalue(), folder)
        st.session_state["invoice_key"] = key
        st.session_state["invoice_view"] = run_invoice(
            saved,
            use_llm=use_grok,
            audit_path=UI_AUDIT,
            db_path=UI_DB,
        )
    show_result(st.session_state["invoice_view"])


if __name__ == "__main__":
    render()
