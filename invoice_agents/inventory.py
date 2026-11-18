import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple


class InventoryItem(NamedTuple):
    stock: int
    unit_price: Decimal


def get_item(db_path: str | Path, item_name: str) -> InventoryItem | None:
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        row = connection.execute(
            "SELECT stock, unit_price FROM inventory WHERE item = ?",
            (item_name,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    stock, unit_price = row
    return InventoryItem(stock=int(stock), unit_price=Decimal(unit_price))
