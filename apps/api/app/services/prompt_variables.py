"""The variables an agent's prompt can cite, in one table.

A prompt is the configuration: a tool reaches the model only when the prompt
names it (``[Herramienta: name]``) and a dynamic block is injected only when the
prompt names it (``[FECHA Y HORA ACTUAL DEL NEGOCIO]`` and friends). Those
decisions read the table below, and so does the panel's prompt editor, through
``public_catalog``. What a prompt may cite, what the editor offers and what the
engine honours are therefore one list, and a variable added here reaches all
three at once.

Adding a variable
    Tools list their own names where they are built
    (``tools/commercial_tools.TOOL_NAMES`` and its ``TOOL_ALIASES``, or
    ``tools/resource_tool.TOOL_NAMES``). A dynamic block is a row in ``BLOCKS``
    and a builder wired to its key in ``crm_prompt_hydrator``. Nothing else
    changes: the editor reads the catalog and matches whatever it is given, and
    ``tests/test_prompt_variables.py`` fails on a variable that is declared but
    not wired.

The markers are Spanish and always were: they are read by the model, not shown
as prose, and the engine matches them regardless of case and accents. Nothing
here is screen copy, so ``public_catalog`` carries no descriptions: those are
i18n keys in the web dictionaries, looked up by token.
"""

import re
from dataclasses import dataclass

from .tools.commercial_tools import TOOL_ALIASES as COMMERCIAL_TOOL_ALIASES
from .tools.commercial_tools import TOOL_NAMES as COMMERCIAL_TOOL_NAMES
from .tools.commercial_tools import strip_accents
from .tools.resource_tool import TOOL_ALIASES as RESOURCE_TOOL_ALIASES
from .tools.resource_tool import TOOL_NAME as RESOURCE_TOOL_NAME

# The three names that carry a value chosen by whoever writes the prompt: a tool
# name, a pipeline stage name, a resource name. The rest are fixed text.
TOOL_PREFIX = "Herramienta"
STAGE_PREFIX = "Etapa"
RESOURCE_PREFIX = "Recurso"
# A lead field is cited by its code, never its label: ``[Campo: 1000]`` names
# the field and ``[Campo: 1000 -> 1001]`` one of its options, so renaming either
# leaves the prompt pointing at the same thing.
FIELD_PREFIX = "Campo"
FIELD_TOKEN_RE = re.compile(rf"\[{FIELD_PREFIX}:\s*(\d{{4,}})(?:\s*->\s*(\d{{4,}}))?\s*\]", re.IGNORECASE)


def field_token(code: int, option: int | None = None) -> str:
    return f"[{FIELD_PREFIX}: {code}]" if option is None else f"[{FIELD_PREFIX}: {code} -> {option}]"


def cited_fields(instructions: str | None) -> list[tuple[int, int | None]]:
    """(field code, option code or None) for every field citation, in order."""
    if not instructions:
        return []
    return [(int(field), int(option) if option else None) for field, option in FIELD_TOKEN_RE.findall(instructions)]

#: The reply sentinel. Not a citation: the model emits it (or calls
#: ``stay_silent``) and the runtime drops the message. A prompt may still name it
#: to ask for it, which is why it is a variable the editor knows.
SILENCE_TOKEN = "[SILENCIO]"

#: The header that frames the injected blocks at request time. It is never part of
#: a stored prompt; seeing it in the editor means the text was copied from a
#: prompt preview and would be dead weight.
DYNAMIC_DATA_HEADER = "[DATOS DINÁMICOS DEL NEGOCIO Y CLIENTE]"


def tool_token(name: str) -> str:
    return f"[{TOOL_PREFIX}: {name}]"


@dataclass(frozen=True)
class BlockVariable:
    """A dynamic block: named by a fixed marker, built per client and per lead."""

    key: str
    token: str
    aliases: tuple[str, ...] = ()


#: The blocks ``hydrate_commercial_prompt`` can inject, each with the markers that
#: cite it. The token is the header its builder emits, so the citation the
#: operator writes and the block the model reads are the same words. The order is
#: the order they are injected in, which is what a stored prompt ends up looking
#: like; keep it.
BLOCKS: tuple[BlockVariable, ...] = (
    BlockVariable("temporal", "[FECHA Y HORA ACTUAL DEL NEGOCIO]"),
    BlockVariable("business_info", "[UBICACIÓN Y DATOS DEL NEGOCIO]", ("[DATOS DEL NEGOCIO]",)),
    BlockVariable("catalog", "[CATÁLOGO OFICIAL DE SERVICIOS Y TARIFAS]", ("[CATÁLOGO OFICIAL]",)),
    BlockVariable("contact_card", "[FICHA COMERCIAL DEL PROSPECTO / CLIENTE]", ("[FICHA COMERCIAL]",)),
    BlockVariable("appointments", "[CITAS ACTIVAS PROGRAMADAS PARA ESTE CLIENTE]", ("[CITAS ACTIVAS]",)),
    BlockVariable("team_notes", "[NOTAS E INTERVENCIONES PREVIAS DEL EQUIPO HUMANO]", ("[NOTAS DEL EQUIPO]",)),
)

TOOL_TOKEN_RE = re.compile(rf"\[{TOOL_PREFIX}:\s*([a-zA-Z0-9_-]+)\s*\]", re.IGNORECASE)


def fold(value: str) -> str:
    """Case- and accent-insensitive, the way a marker is compared to its table."""
    return strip_accents(value)


def _tool_table() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """(canonical name, marker, older spellings) for every tool, in offer order."""
    rows: list[tuple[str, str, tuple[str, ...]]] = [
        (name, tool_token(name), COMMERCIAL_TOOL_ALIASES.get(name, ()))
        for name in COMMERCIAL_TOOL_NAMES
    ]
    rows.append((RESOURCE_TOOL_NAME, tool_token(RESOURCE_TOOL_NAME), RESOURCE_TOOL_ALIASES))
    return tuple(rows)


TOOLS: tuple[tuple[str, str, tuple[str, ...]], ...] = _tool_table()

# Folded marker -> the block it cites. Folded because a prompt is written by
# hand: `[CATÁLOGO OFICIAL]` in any casing is the same citation.
_BLOCK_BY_MARKER: dict[str, str] = {
    fold(marker): block.key
    for block in BLOCKS
    for marker in (block.token, *block.aliases)
}

# Folded name -> the canonical tool it belongs to, so the editor can colour an
# older spelling as the tool it still means.
_TOOL_CANONICAL: dict[str, str] = {
    fold(name): canonical
    for canonical, _, aliases in TOOLS
    for name in (canonical, *aliases)
}


def canonical_tool(name: str) -> str | None:
    """The name a tool is shown under, or None when the release has no such tool."""
    return _TOOL_CANONICAL.get(fold(name))


def declared_tools(instructions: str | None) -> list[str]:
    """Tool names cited via [Herramienta: name], in order, without repeats.

    Reported exactly as the prompt wrote them: the tool loop shows the model the
    name the prompt knows, so renaming a tool here would be a silent change to
    what an existing prompt asks for. ``canonical_tool`` is how the panel maps a
    citation onto the tool it belongs to.
    """
    if not instructions:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for match in TOOL_TOKEN_RE.findall(instructions):
        name = match.strip().lower()
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def has_declarative_tools(instructions: str | None) -> bool:
    """True when the prompt cites a tool, which is what opens the declarative
    path. The name itself is not checked: a prompt that names a tool this release
    dropped still opens the path, and builds whatever tools it does know."""
    return bool(declared_tools(instructions))


def cited_blocks(instructions: str | None) -> set[str]:
    """Keys of the blocks a prompt names, however it spells them."""
    if not instructions:
        return set()
    folded = fold(instructions)
    return {key for marker, key in _BLOCK_BY_MARKER.items() if marker in folded}


def public_catalog() -> list[dict]:
    """What the prompt editor offers, with no screen copy attached.

    ``token`` is text to insert verbatim; ``template`` is the shape of a
    variable the writer completes with a name they pick. ``aliases`` are the
    older markers that cite the same variable, listed so the editor can
    recognise a prompt written against an earlier release. ``picker`` names a
    variable that brings its own chooser, so the panel does not have to know
    which tool that is.
    """
    catalog: list[dict] = [
        {
            "kind": "tool",
            "value": name,
            "token": token,
            "aliases": [tool_token(alias) for alias in aliases],
            "picker": "resource" if name == RESOURCE_TOOL_NAME else None,
        }
        for name, token, aliases in TOOLS
    ]
    catalog += [
        {
            "kind": "block",
            "value": block.key,
            "token": block.token,
            "aliases": list(block.aliases),
            "picker": None,
        }
        for block in BLOCKS
    ]
    catalog += [
        {
            "kind": kind,
            "value": prefix,
            "template": f"[{prefix}: {{name}}]",
            "aliases": [],
            "picker": None,
        }
        for kind, prefix in (("stage", STAGE_PREFIX), ("resource", RESOURCE_PREFIX), ("field", FIELD_PREFIX))
    ]
    catalog += [
        {"kind": "control", "value": SILENCE_TOKEN, "token": SILENCE_TOKEN, "aliases": [], "picker": None},
    ]
    return catalog
