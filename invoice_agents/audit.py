import json
from pathlib import Path


def append_decision(audit_path: str | Path, decision: dict) -> None:
    path = Path(audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(decision) + "\n")


def read_decisions(audit_path: str | Path) -> list[dict]:
    path = Path(audit_path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
