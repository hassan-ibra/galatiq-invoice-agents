import json
import os
import subprocess
import sys
from pathlib import Path

from invoice_agents.agents.approval import approve
from invoice_agents.agents.ingestion import ingest
from invoice_agents.agents.payment import pay
from invoice_agents.agents.validation import validate
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
