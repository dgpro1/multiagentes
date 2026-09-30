"""The declarative ``enviar_recurso`` tool: send a file or a link from the
client's resource library.

The agent's prompt cites the tool (``[Herramienta: enviar_recurso]``) and, one
per line, the resources it may send (``[Recurso: Catálogo 2026]``). Only those
reach the model, as an enum, so it can neither invent a resource nor send one
the prompt did not hand it.

The handler does no I/O (see ``ToolSpec``): it queues the choice. After the
loop, ``deliver_resources`` reads each file from the client's own R2 bucket
into a ``ToolFile`` (delivered like any tool-produced file) and appends each
link's rendered text to the reply, verbatim, so the model never paraphrases an
address.
"""

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models import Agent, Client, ClientResource, Contact
from .. import resource_storage as storage
from ..resources_catalog import list_resources, render_link_text
from ..tool_files import MAX_TOOL_FILES, ToolFile
from .specs import ToolSpec

logger = logging.getLogger(__name__)

TOOL_NAME = "enviar_recurso"
TOOL_ALIASES = ("send_resource",)
# Canonical name first: a caller that wants to know which name to show reads
# TOOL_NAME, one that wants every name the prompt may cite reads this tuple.
TOOL_NAMES = (TOOL_NAME, *TOOL_ALIASES)
DECLARED_RESOURCE_RE = re.compile(r"\[Recurso:\s*([^\]\n]+?)\s*\]", re.IGNORECASE)


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value or "")
    return "".join(c for c in value if unicodedata.category(c) != "Mn").lower().strip()


def declared_resources(instructions: str | None) -> list[str]:
    """Resource names cited via [Recurso: name], in order, without repeats."""
    seen: set[str] = set()
    names: list[str] = []
    for raw in DECLARED_RESOURCE_RE.findall(instructions or ""):
        name = raw.strip()
        if name and _fold(name) not in seen:
            seen.add(_fold(name))
            names.append(name)
    return names


@dataclass
class ResourceEffects:
    """The resources the model chose during one reply, by id, in order."""

    chosen: list[ClientResource] = field(default_factory=list)


def available_resources(db: Session, client: Client, agent: Agent) -> list[ClientResource]:
    """The active resources the prompt cites. Files need a connected bucket to be sent."""
    cited = {_fold(name) for name in declared_resources(agent.instructions)}
    if not cited:
        return []
    connected = bool(client.storage_connection and client.storage_connection.status == "connected")
    return [
        row for row in list_resources(db, client, active_only=True)
        if _fold(row.name) in cited and (row.kind == "link" or connected)
    ]


def build_resource_spec(
    db: Session, client: Client, agent: Agent, tool_name: str, effects: ResourceEffects
) -> ToolSpec | None:
    rows = available_resources(db, client, agent)
    if not rows:
        return None
    by_name = {_fold(row.name): row for row in rows}

    def handler(args: dict) -> tuple[str, bool]:
        wanted = str(args.get("resource") or args.get("recurso") or "").strip()
        row = by_name.get(_fold(wanted))
        if not row:
            options = ", ".join(r.name for r in rows)
            return f"Recurso desconocido '{wanted}'. Opciones válidas: {options}.", True
        if any(chosen.id == row.id for chosen in effects.chosen):
            return f"'{row.name}' ya está en cola para enviarse en esta respuesta.", False
        if row.kind == "file" and sum(1 for c in effects.chosen if c.kind == "file") >= MAX_TOOL_FILES:
            return f"No se pueden enviar más de {MAX_TOOL_FILES} archivos en una respuesta.", True
        effects.chosen.append(row)
        if row.kind == "link":
            return (
                f"Enlace '{row.name}' en cola: se añadirá automáticamente al final de tu respuesta tal como está "
                "guardado. No escribas tú la dirección; basta con una frase breve que lo presente."
            ), False
        return (
            f"Archivo '{row.name}' ({row.media_kind}) en cola: se enviará adjunto justo después de tu respuesta. "
            "No inventes enlaces para él; basta con una frase breve que lo presente."
        ), False

    catalog = "\n".join(
        f"- {row.name} ({'enlace' if row.kind == 'link' else row.media_kind}): {row.description or 'sin descripción'}"
        for row in rows
    )
    return ToolSpec(
        name=tool_name,
        description=(
            "Envía al cliente un archivo (imagen, video, documento) o un enlace de la biblioteca del negocio. "
            "Úsala cuando el cliente lo pida o cuando la descripción del recurso indique que corresponde. "
            "Recursos disponibles:\n" + catalog
        ),
        input_schema={
            "type": "object",
            "properties": {
                "resource": {
                    "type": "string",
                    "enum": [row.name for row in rows],
                    "description": "Nombre exacto del recurso a enviar.",
                },
            },
            "required": ["resource"],
        },
        handler=handler,
    )


@dataclass
class DeliveredResources:
    reply_text: str
    files: list[ToolFile]
    failures: list[str]


async def deliver_resources(
    client: Client, contact: Contact | None, effects: ResourceEffects, reply_text: str
) -> DeliveredResources:
    """Turn the queued choices into what goes out: link text appended to the
    reply, file bytes read from the client's bucket. A file that cannot be read
    is reported, never fatal: the rest of the reply still goes out."""
    files: list[ToolFile] = []
    failures: list[str] = []
    text = reply_text or ""
    for row in effects.chosen:
        if row.kind == "link":
            link_text = render_link_text(row, client, contact)
            if row.url and row.url in text:
                continue
            text = f"{text}\n\n{link_text}".strip() if text else link_text
            continue
        try:
            store = storage.for_client(client)
            data = await asyncio.to_thread(store.get, row.storage_key or "")
        except (HTTPException, storage.StorageError) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            logger.warning("Could not read resource %s of client %s: %s", row.id, client.id, detail)
            failures.append(f"No se pudo enviar el recurso '{row.name}': {detail}")
            continue
        files.append(ToolFile(data=data, mime=row.mime or "application/octet-stream", filename=row.filename or row.name))
    return DeliveredResources(reply_text=text, files=files, failures=failures)
