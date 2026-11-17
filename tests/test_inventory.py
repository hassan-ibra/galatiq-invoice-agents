import sqlite3
from decimal import Decimal

from invoice_agents.inventory import get_item
from setup_inventory import create_inventory


def test_widget_a_stock_and_price(tmp_path):
    db_path = tmp_path / "inventory.db"
    create_inventory(db_path)

    item = get_item(db_path, "WidgetA")

    assert item is not None
    assert item.stock == 15
    assert type(item.stock) is int
    assert item.unit_price == Decimal("250.00")
    assert type(item.unit_price) is Decimal


def test_fake_item_has_zero_stock(tmp_path):
    db_path = tmp_path / "inventory.db"
    create_inventory(db_path)

    item = get_item(db_path, "FakeItem")

    assert item is not None
    assert item.stock == 0
    assert item.unit_price == Decimal("1000.00")


def test_seed_rows(tmp_path):
    db_path = tmp_path / "inventory.db"
    create_inventory(db_path)

    assert get_item(db_path, "WidgetB").stock == 10
    assert get_item(db_path, "WidgetB").unit_price == Decimal("500.00")
    assert get_item(db_path, "GadgetX").stock == 5
    assert get_item(db_path, "GadgetX").unit_price == Decimal("750.00")


def test_unit_price_is_stored_as_text(tmp_path):
    db_path = tmp_path / "inventory.db"
    create_inventory(db_path)

    connection = sqlite3.connect(db_path)
    row = connection.execute(
        "SELECT unit_price, typeof(unit_price) FROM inventory WHERE item = 'WidgetA'"
    ).fetchone()
    connection.close()

    assert row == ("250.00", "text")


def test_unknown_item_returns_none(tmp_path):
    db_path = tmp_path / "inventory.db"
    create_inventory(db_path)

    assert get_item(db_path, "SuperGizmo") is None
    assert get_item(db_path, "Shipping") is None


def test_lookup_is_case_sensitive(tmp_path):
    db_path = tmp_path / "inventory.db"
    create_inventory(db_path)

    assert get_item(db_path, "widgeta") is None
    assert get_item(db_path, "WidgetA").stock == 15


def test_create_inventory_replaces_existing_rows(tmp_path):
    db_path = tmp_path / "inventory.db"
    create_inventory(db_path)

    connection = sqlite3.connect(db_path)
    connection.execute("INSERT INTO inventory VALUES ('Extra', 1, '1.00')")
    connection.commit()
    connection.close()

    create_inventory(db_path)

    assert get_item(db_path, "Extra") is None
    assert get_item(db_path, "WidgetA").stock == 15
    connection = sqlite3.connect(db_path)
    count = connection.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
    connection.close()
    assert count == 4
