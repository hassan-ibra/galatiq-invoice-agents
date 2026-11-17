import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/inventory.db")

SEED = (
    ("WidgetA", 15, "250.00"),
    ("WidgetB", 10, "500.00"),
    ("GadgetX", 5, "750.00"),
    ("FakeItem", 0, "1000.00"),
)


def create_inventory(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE IF EXISTS inventory")
        connection.execute(
            """
            CREATE TABLE inventory (
                item TEXT PRIMARY KEY,
                stock INTEGER NOT NULL,
                unit_price TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO inventory (item, stock, unit_price) VALUES (?, ?, ?)",
            SEED,
        )
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    create_inventory()
    connection = sqlite3.connect(DEFAULT_DB_PATH)
    try:
        rows = connection.execute(
            "SELECT item, stock, unit_price FROM inventory ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()
    for item, stock, unit_price in rows:
        print(f"{item} {stock} {unit_price}")


if __name__ == "__main__":
    main()
