import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from invoice_agents.agents.approval import approve
from invoice_agents.agents.ingestion import ingest
from invoice_agents.agents.payment import pay
from invoice_agents.agents.validation import validate
from invoice_agents.audit import read_decisions
from invoice_agents.graph import build_graph
from invoice_agents.models import PipelineState
from setup_inventory import create_inventory

ROOT = Path(__file__).resolve().parents[1]
INVOICE = ROOT / "data" / "invoices" / "invoice_1001.txt"


def _track(order: list[str], name: str, function):
    def wrapped(state):
        order.append(name)
        return function(state)

    return wrapped


def test_nodes_run_in_order(tmp_path, monkeypatch):
    import invoice_agents.graph as graph_module

    order: list[str] = []
    monkeypatch.setattr(graph_module, "ingest", _track(order, "ingestion", ingest))
    monkeypatch.setattr(graph_module, "validate", _track(order, "validation", validate))
    monkeypatch.setattr(graph_module, "approve", _track(order, "approval", approve))
    monkeypatch.setattr(graph_module, "pay", _track(order, "payment", pay))

    db_path = tmp_path / "inventory.db"
    create_inventory(db_path)
    state = PipelineState(
        source_path=str(INVOICE),
        db_path=str(db_path),
        audit_path=str(tmp_path / "audit.jsonl"),
    )

    build_graph().invoke(state)

    assert order == ["ingestion", "validation", "approval", "payment"]


def test_main_invoice_1001_approved_and_paid(tmp_path):
    db_path = tmp_path / "inventory.db"
    audit_path = tmp_path / "audit.jsonl"
    create_inventory(db_path)
    env = os.environ.copy()
    env.pop("XAI_API_KEY", None)

    completed = subprocess.run(
        [
            sys.executable,
            "main.py",
            "--invoice_path=data/invoices/invoice_1001.txt",
            f"--db={db_path}",
            f"--audit={audit_path}",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["invoice_number"] == "INV-1001"
    assert payload["status"] == "approved"
    assert payload["payment_result"]["status"] == "success"
    assert payload["payment_result"]["amount"] == "5000.00"


def _run_main(db_path: Path, audit_path: Path, filename: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("XAI_API_KEY", None)
    return subprocess.run(
        [
            sys.executable,
            "main.py",
            f"--invoice_path=data/invoices/{filename}",
            f"--db={db_path}",
            f"--audit={audit_path}",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_second_copy_is_not_paid_again(tmp_path):
    pairs = (
        ("invoice_1011.txt", "invoice_1011.pdf"),
        ("invoice_1012.txt", "invoice_1012.pdf"),
    )
    for index, (first_name, second_name) in enumerate(pairs):
        db_path = tmp_path / f"{index}.db"
        audit_path = tmp_path / f"{index}.jsonl"
        create_inventory(db_path)

        first = _run_main(db_path, audit_path, first_name)
        second = _run_main(db_path, audit_path, second_name)

        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        paid_lines = [
            line
            for line in f"{first.stderr}\n{second.stderr}".splitlines()
            if line.startswith("Paid ")
        ]
        assert len(paid_lines) == 1
        reasons = [decision.get("reason", "") for decision in read_decisions(audit_path)]
        assert any("already paid" in reason.lower() for reason in reasons)
        assert json.loads(first.stdout)["payment_result"]["status"] == "success"
        assert json.loads(second.stdout)["payment_result"] is None


# Expected end-to-end results from PLAN.md, using default flags.
SAMPLES = [
    ("invoice_1001.txt", {"number": "INV-1001", "status": "approved", "paid": "5000.00", "late": False}),
    ("invoice_1002.txt", {"number": "INV-1002", "status": "hold", "paid": None, "rules": [1, 12]}),
    (
        "invoice_1003.txt",
        {"number": "INV-1003", "status": "rejected", "paid": None, "rules": [5], "reasons": ["FakeItem"]},
    ),
    (
        "invoice_1004.json",
        {"number": "INV-1004", "status": "hold", "paid": None, "revision": "R1", "void": True, "rules": [10]},
    ),
    (
        "invoice_1004_revised.json",
        {"number": "INV-1004", "status": "hold", "paid": None, "revision": "R1", "void": True, "rules": [10]},
    ),
    ("invoice_1005.json", {"number": "INV-1005", "status": "hold", "paid": None, "rules": [1, 10, 12]}),
    ("invoice_1006.csv", {"number": "INV-1006", "status": "approved", "paid": "2750.00"}),
    (
        "invoice_1007.csv",
        {"number": "INV-1007", "status": "rejected", "paid": None, "reasons": ["-110.00"]},
    ),
    (
        "invoice_1008.txt",
        {
            "number": "INV-1008",
            "status": "rejected",
            "paid": None,
            "reasons": ["SuperGizmo", "MegaSprocket"],
        },
    ),
    ("invoice_1009.json", {"number": "INV-1009", "status": "rejected", "paid": None, "rules": [6]}),
    (
        "invoice_1010.txt",
        {"number": "INV-1010", "status": "hold", "paid": None, "rules": [9], "no_reject": True},
    ),
    (
        "invoice_1011.txt",
        {
            "number": "INV-1011",
            "status": "approved",
            "paid": "3000.00",
            "files": ["invoice_1011.txt", "invoice_1011.pdf"],
        },
    ),
    (
        "invoice_1011.pdf",
        {
            "number": "INV-1011",
            "status": "approved",
            "paid": "3000.00",
            "files": ["invoice_1011.txt", "invoice_1011.pdf"],
        },
    ),
    (
        "invoice_1012.txt",
        {"number": "INV-1012", "status": "approved", "paid": "9975.00", "corrections": True},
    ),
    (
        "invoice_1012.pdf",
        {"number": "INV-1012", "status": "approved", "paid": "9975.00", "corrections": True},
    ),
    ("invoice_1013.json", {"number": "INV-1013", "status": "rejected", "paid": None, "reasons": ["50.00"]}),
    ("invoice_1013.pdf", {"number": "INV-1013", "status": "rejected", "paid": None, "reasons": ["50.00"]}),
    (
        "invoice_1014.xml",
        {"number": "INV-1014", "status": "hold", "paid": None, "rules": [3], "currency": "EUR"},
    ),
    ("invoice_1015.csv", {"number": "INV-1015", "status": "approved", "paid": "6500.00"}),
    (
        "invoice_1016.json",
        {"number": "INV-1016", "status": "rejected", "paid": None, "reasons": ["WidgetC"]},
    ),
]


def _run_sample(tmp_path, filename: str) -> tuple[PipelineState, list[dict]]:
    db_path = tmp_path / "inventory.db"
    audit_path = tmp_path / "audit.jsonl"
    create_inventory(db_path)
    state = PipelineState(
        source_path=str(ROOT / "data" / "invoices" / filename),
        db_path=str(db_path),
        audit_path=str(audit_path),
    )
    result = build_graph().invoke(state)
    if isinstance(result, dict):
        result = PipelineState.model_validate(result)
    return result, read_decisions(audit_path)


def _paid_amount(state: PipelineState) -> str | None:
    if state.payment_result is None:
        return None
    return state.payment_result.get("amount")


@pytest.mark.parametrize("filename,expected", SAMPLES, ids=[name for name, _expected in SAMPLES])
def test_sample_file(tmp_path, filename, expected):
    state, decisions = _run_sample(tmp_path, filename)
    invoice = state.invoice
    reasons = " ".join(finding.reason for finding in state.findings)
    paid_lines = [decision for decision in decisions if decision.get("paid") is True]

    assert invoice is not None
    assert invoice.invoice_number == expected["number"]
    assert state.status == expected["status"]
    assert _paid_amount(state) == expected["paid"]
    assert len(paid_lines) == (0 if expected["paid"] is None else 1)
    if "late" in expected:
        assert state.late is expected["late"]
    if "revision" in expected:
        assert invoice.revision == expected["revision"]
    if expected.get("void"):
        assert state.void_original is True
        assert any("voided" in decision.get("reason", "") for decision in decisions)
    if "rules" in expected:
        found = {finding.rule for finding in state.findings}
        assert set(expected["rules"]).issubset(found)
    for snippet in expected.get("reasons", []):
        assert snippet in reasons
    if expected.get("no_reject"):
        assert not any(finding.outcome == "reject" for finding in state.findings)
    if "files" in expected:
        assert {Path(path).name for path in invoice.source_files} == set(expected["files"])
    if expected.get("corrections"):
        assert invoice.corrections
    if "currency" in expected:
        assert invoice.currency == expected["currency"]
