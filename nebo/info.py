"""Справочники из tar1090-db: типы самолётов и авиакомпании по ICAO-коду."""
import json
import re
from pathlib import Path

DATA = Path(__file__).parent / "data"
_CALLSIGN_RE = re.compile(r"^([A-Z]{3})\d")


def _pretty(name: str) -> str:
    """«AIRBUS A-320neo» → «Airbus A-320neo»: капсом в базе записан только производитель."""
    words = name.split()
    return " ".join(w.capitalize() if w.isupper() and w.isalpha() and len(w) > 2 else w for w in words)


class AircraftInfo:
    def __init__(self):
        self.types: dict[str, list] = json.loads((DATA / "types.json").read_text())
        self.operators: dict[str, list] = json.loads((DATA / "operators.json").read_text())

    def type_name(self, code: str | None) -> str | None:
        entry = self.types.get(code or "")
        return _pretty(entry[0]) if entry else None

    def type_desc(self, code: str | None) -> tuple[str | None, str | None]:
        """Класс (например L2J — сухопутный, 2 реактивных) и категория турбулентности (L/M/H)."""
        entry = self.types.get(code or "")
        return (entry[1], entry[2]) if entry else (None, None)

    def operator_code(self, callsign: str | None) -> str | None:
        m = _CALLSIGN_RE.match(callsign or "")
        return m.group(1) if m and m.group(1) in self.operators else None

    def operator(self, callsign: str | None) -> str | None:
        code = self.operator_code(callsign)
        return self.operators[code][0] if code else None
