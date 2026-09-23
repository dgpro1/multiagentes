"""The API coverage map: every panel screen names its API route, and every
API route in the versioned and credential surfaces is named by the map.

``docs/api-coverage.md`` is the checklist that says a panel screen without
an API route is a bug. This test reads it in both directions against the
OpenAPI schema, so adding a route without documenting its screen — or a
screen without its route — fails loudly.
"""

import re
from pathlib import Path

COVERAGE = Path(__file__).resolve().parents[3] / "docs" / "api-coverage.md"

_ROUTE = re.compile(r"`([A-Z]+)\s+([^`]+)`")
_COVERED_PREFIXES = ("/api/v1", "/api/integrations", "/api/oauth")


def _rows():
    section = ""
    for line in COVERAGE.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[1].startswith("---"):
            continue
        yield section, cells[0], cells[1]


def test_every_listed_route_exists_in_the_schema():
    from app.main import app

    schema = app.openapi()
    checked = 0
    for section, screen, route_cell in _rows():
        for method, path in _ROUTE.findall(route_cell):
            assert path in schema["paths"], f"{screen} names {method} {path}, missing from OpenAPI"
            assert method.lower() in schema["paths"][path], f"{screen} names {method} {path}, method missing"
            checked += 1
    assert checked > 40, "The coverage map lists no routes"


def test_every_route_is_listed_and_panel_only_has_reasons():
    from app.main import app

    schema = app.openapi()
    listed: set[tuple[str, str]] = set()
    panel_only = 0
    for section, screen, route_cell in _rows():
        found = _ROUTE.findall(route_cell)
        if found:
            for method, path in found:
                listed.add((method, path))
        elif section == "Deliberately panel-only":
            assert route_cell, f"{screen} is panel-only without a reason"
            panel_only += 1
    assert panel_only > 10, "The panel-only list is missing"

    missing = [
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        if path.startswith(_COVERED_PREFIXES)
        for method in operations
        if (method.upper(), path) not in listed
    ]
    assert missing == [], f"API routes without a coverage row: {missing}"
