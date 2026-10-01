"""Every error the API raises for the panel has its Spanish in the web table.

The API answers in English; ``apps/web/lib/i18n/api-errors.ts`` translates the
message before a Spanish screen shows it. This reads every ``HTTPException``
the API raises with a fixed message or an f-string and checks the table has it,
so a new error cannot reach a Spanish screen in English.
"""

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
TABLE = Path(__file__).resolve().parents[3] / "apps" / "web" / "lib" / "i18n" / "api-errors.ts"

# Machine codes a screen recognises and words itself.
SCREEN_WORDED = {"storage_not_connected"}


def _raised() -> tuple[set[str], set[str]]:
    fixed: set[str] = set()
    templates: set[str] = set()
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "HTTPException"):
                continue
            details = [kw.value for kw in node.keywords if kw.arg == "detail"] + node.args[1:2]
            for detail in details:
                if isinstance(detail, ast.Constant) and isinstance(detail.value, str):
                    fixed.add(detail.value)
                elif isinstance(detail, ast.JoinedStr):
                    templates.add("".join(part.value if isinstance(part, ast.Constant) else "{}" for part in detail.values))
    return fixed - SCREEN_WORDED, templates


def _table() -> tuple[set[str], set[str]]:
    text = TABLE.read_text(encoding="utf-8")
    exact = set(re.findall(r'^  "((?:[^"\\]|\\.)*)": "', text, re.MULTILINE))
    templates = set(re.findall(r'^  \["((?:[^"\\]|\\.)*)", "', text, re.MULTILINE))
    return exact, templates


def test_every_fixed_error_has_its_spanish():
    fixed, _ = _raised()
    exact, _ = _table()
    assert fixed, "no HTTPException messages were found; the scan is broken"
    missing = sorted(fixed - exact)
    assert not missing, f"add these to apps/web/lib/i18n/api-errors.ts: {missing}"


def test_every_error_built_with_values_has_a_template():
    _, templates = _raised()
    _, table = _table()
    missing = sorted(templates - table)
    assert not missing, f"add these templates to apps/web/lib/i18n/api-errors.ts: {missing}"
