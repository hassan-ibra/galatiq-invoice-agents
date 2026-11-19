import json
from pathlib import Path

from app import (
    GREEN,
    group_findings,
    grok_key_error,
    run_events,
    run_invoice,
    run_log_html,
    save_upload,
    status_badge,
)
from setup_inventory import create_inventory

INVOICE = Path(__file__).resolve().parents[1] / "data" / "invoices" / "invoice_1001.txt"


def test_grok_key_error_only_when_toggle_is_on_and_key_is_missing():
    assert grok_key_error(False, None) is None
    assert grok_key_error(False, "") is None
    assert grok_key_error(True, None) is not None
    assert "XAI_API_KEY" in grok_key_error(True, "  ")
    assert grok_key_error(True, "secret") is None


def test_status_badge_colors():
    assert status_badge("approved", paid=True) == ("paid", GREEN)
    assert status_badge("approved", paid=False) == ("approved", GREEN)
    hold_label, hold_color = status_badge("hold", paid=False)
    rejected_label, rejected_color = status_badge("rejected", paid=False)

    assert hold_label == "hold"
    assert rejected_label == "rejected"
    assert hold_color != GREEN
    assert rejected_color != GREEN
    assert hold_color != rejected_color


def test_save_upload_keeps_the_file_name(tmp_path):
    path = save_upload("invoice_1001.txt", b"hello\n", tmp_path)

    assert path.name == "invoice_1001.txt"
    assert path.read_bytes() == b"hello\n"


def test_run_invoice_shows_paid_result_and_only_this_runs_events(tmp_path):
    db_path = tmp_path / "inventory.db"
    audit_path = tmp_path / "ui.jsonl"
    create_inventory(db_path)
    audit_path.write_text(
        json.dumps({"run_id": "other", "agent": "payment", "invoice_number": "INV-9999", "reason": "not this run"})
        + "\n",
        encoding="utf-8",
    )

    view = run_invoice(INVOICE, use_llm=False, audit_path=audit_path, db_path=db_path)

    assert view["payload"]["invoice_number"] == "INV-1001"
    assert view["payload"]["status"] == "approved"
    assert view["vendor"] == "Widgets Inc."
    assert view["total"] == "5000.00"
    assert view["badge"] == "paid"
    assert view["color"] == GREEN
    assert view["events"] == run_events(audit_path, view["run_id"])
    assert view["events"].startswith(f"=== Run {view['run_id']} | INV-1001 |")
    assert "payment:" in view["events"]
    assert "INV-9999" not in view["events"]
    assert "not this run" not in view["events"]


def _finding(outcome: str, reason: str, rule: int = 6) -> dict:
    return {"rule": rule, "outcome": outcome, "reason": reason}


def test_group_findings_leads_with_the_most_severe_and_hides_empty_sections():
    grouped = group_findings(
        [
            _finding("info", "total 15000.00 is over 10000.00", rule=12),
            _finding("hold", "GadgetX quantity 20 exceeds stock 5", rule=1),
            _finding("reject", "FakeItem has zero stock", rule=5),
            _finding("hold", "currency EUR is not USD", rule=3),
            _finding("info", "due date 2026-01-19 is before 2026-01-20", rule=11),
        ]
    )

    assert grouped["headline"] == "FakeItem has zero stock."
    assert grouped["sections"] == [
        {
            "title": "Why rejected",
            "reasons": ["FakeItem has zero stock"],
        },
        {
            "title": "Also needs attention",
            "reasons": [
                "GadgetX quantity 20 exceeds stock 5",
                "currency EUR is not USD",
            ],
        },
        {
            "title": "Notes",
            "reasons": [
                "total 15000.00 is over 10000.00",
                "due date 2026-01-19 is before 2026-01-20",
            ],
        },
    ]


def test_group_findings_lists_every_reject_and_summarizes_several():
    grouped = group_findings(
        [
            _finding("reject", "vendor name is empty"),
            _finding("reject", "WidgetA quantity -5 is not a positive integer"),
            _finding("info", "total 100000.00 is over 10000.00", rule=12),
        ]
    )

    assert grouped["headline"] == (
        "Rejected for 2 problems: vendor name is empty, "
        "and WidgetA quantity -5 is not a positive integer."
    )
    assert grouped["sections"] == [
        {
            "title": "Why rejected",
            "reasons": [
                "vendor name is empty",
                "WidgetA quantity -5 is not a positive integer",
            ],
        },
        {
            "title": "Notes",
            "reasons": ["total 100000.00 is over 10000.00"],
        },
    ]


def test_group_findings_summarizes_items_missing_from_inventory():
    grouped = group_findings(
        [
            _finding("reject", "FakeItem is not in inventory", rule=5),
            _finding("reject", "OtherItem is not in inventory", rule=5),
        ]
    )

    assert grouped["headline"] == (
        "Rejected for 2 problems: 2 items are not in inventory."
    )
    assert grouped["sections"] == [
        {
            "title": "Why rejected",
            "reasons": [
                "FakeItem is not in inventory",
                "OtherItem is not in inventory",
            ],
        }
    ]


def test_group_findings_uses_a_hold_when_nothing_was_rejected():
    grouped = group_findings(
        [
            _finding("info", "total 15000.00 is over 10000.00", rule=12),
            _finding("hold", "GadgetX quantity 20 exceeds stock 5", rule=1),
        ]
    )

    assert grouped["headline"] == "GadgetX quantity 20 exceeds stock 5."
    assert grouped["sections"] == [
        {
            "title": "Also needs attention",
            "reasons": ["GadgetX quantity 20 exceeds stock 5"],
        },
        {
            "title": "Notes",
            "reasons": ["total 15000.00 is over 10000.00"],
        },
    ]


def test_group_findings_lists_every_hold_and_summarizes_several():
    grouped = group_findings(
        [
            _finding("hold", "GadgetX quantity 20 exceeds stock 5", rule=1),
            _finding("hold", "currency EUR is not USD", rule=3),
            _finding("info", "total 15000.00 is over 10000.00", rule=12),
        ]
    )

    assert grouped["headline"] == (
        "Held for 2 problems: GadgetX quantity 20 exceeds stock 5, "
        "and currency EUR is not USD."
    )
    assert grouped["sections"] == [
        {
            "title": "Also needs attention",
            "reasons": [
                "GadgetX quantity 20 exceeds stock 5",
                "currency EUR is not USD",
            ],
        },
        {
            "title": "Notes",
            "reasons": ["total 15000.00 is over 10000.00"],
        },
    ]


def test_run_log_wraps_long_lines():
    rendered = run_log_html("short\n" + ("x" * 200) + " <tag>")

    assert "pre-wrap" in rendered
    assert "overflow-wrap:anywhere" in rendered
    assert "x" * 200 in rendered
    assert "&lt;tag&gt;" in rendered
    assert "<tag>" not in rendered


def test_group_findings_with_nothing_to_show():
    assert group_findings([]) == {"headline": None, "sections": []}


def test_group_findings_drops_duplicate_total_mismatch_hold():
    grouped = group_findings(
        [
            _finding(
                "hold",
                "subtotal + tax 15635.00 does not equal total 15525.00",
            ),
            _finding(
                "reject",
                "stated total 15525.00 minus expected 15635.00 is -110.00",
                rule=4,
            ),
            _finding("hold", "WidgetA quantity 20 exceeds stock 15", rule=1),
            _finding("info", "total 15525.00 is over 10000.00", rule=12),
        ]
    )

    assert grouped["headline"] == (
        "Stated total 15525.00 minus expected 15635.00 is -110.00."
    )
    assert grouped["sections"] == [
        {
            "title": "Why rejected",
            "reasons": [
                "stated total 15525.00 minus expected 15635.00 is -110.00"
            ],
        },
        {
            "title": "Also needs attention",
            "reasons": ["WidgetA quantity 20 exceeds stock 15"],
        },
        {
            "title": "Notes",
            "reasons": ["total 15525.00 is over 10000.00"],
        },
    ]


def test_group_findings_keeps_total_hold_when_validation_did_not_reject_it():
    grouped = group_findings(
        [
            _finding("hold", "subtotal + tax 1000.00 does not equal total -250.00"),
            _finding(
                "reject",
                "stated subtotal 1000.00 does not equal line sum -250.00",
            ),
            _finding(
                "hold",
                "stated subtotal 1000.00 does not equal line sum -250.00",
            ),
        ]
    )

    assert grouped["headline"] == (
        "Stated subtotal 1000.00 does not equal line sum -250.00."
    )
    assert grouped["sections"] == [
        {
            "title": "Why rejected",
            "reasons": ["stated subtotal 1000.00 does not equal line sum -250.00"],
        },
        {
            "title": "Also needs attention",
            "reasons": [
                "subtotal + tax 1000.00 does not equal total -250.00",
                "stated subtotal 1000.00 does not equal line sum -250.00",
            ],
        }
    ]
