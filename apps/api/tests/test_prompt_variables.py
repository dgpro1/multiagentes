"""The one table of prompt variables.

A prompt is the configuration, so a variable is easy to add in one place and
forget in another, and every way of forgetting fails quietly: a tool the editor
offers that the loop never builds, a block a prompt cites that nothing injects,
a tool built behind a gate nobody listed. These tests are the guard for that,
and they read the table the panel's editor reads.
"""

import re
from pathlib import Path

import pytest

from app.models import Agency, Agent, Client, ClientResource, Contact, Conversation
from app.services import crm_prompt_hydrator, prompt_variables
from app.services.knowledge import build_system_prompt
from app.services.prompt_variables import (
    BLOCKS,
    SILENCE_TOKEN,
    TOOLS,
    canonical_tool,
    cited_blocks,
    declared_tools,
    fold,
    public_catalog,
)
from app.services.tools.commercial_tools import CommercialEffects, build_commercial_tools
from app.services.tools.resource_tool import TOOL_NAMES as RESOURCE_TOOL_NAMES
from conftest import TestingSession

# The gates ``build_commercial_tools`` opens on, read from the source. A tool
# added there without a row in TOOL_NAMES would be offered by nobody and built
# for everybody who typed the marker by hand.
_GATE_RE = re.compile(r'if "([a-z_]+)" in declared_set')
_ALIAS_GATE_RE = re.compile(r'or "([a-z_]+)" in declared_set')
_COMMERCIAL_SOURCE = (
    Path(__file__).resolve().parents[1] / "app" / "services" / "tools" / "commercial_tools.py"
).read_text(encoding="utf-8")


@pytest.fixture
def db_session():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


def make_client(db, name="Dental Marbella", instructions=""):
    import uuid

    agency = Agency(name="Agency " + name, slug="agency-" + str(uuid.uuid4())[:8])
    db.add(agency)
    db.flush()
    client = Client(
        agency_id=agency.id,
        name=name,
        portal_slug="client-" + str(uuid.uuid4())[:8],
        timezone="America/Santiago",
        address="Dinamarca 621, Temuco",
    )
    db.add(client)
    db.flush()
    agent = Agent(
        client_id=client.id,
        agency_id=agency.id,
        name="Anita",
        instructions=instructions,
        model="gpt-4.1-mini",
        provider="openrouter",
    )
    db.add(agent)
    db.flush()
    contact = Contact(client_id=client.id, name="Camila Soto", phone="+56912345678")
    db.add(contact)
    db.flush()
    conversation = Conversation(
        agency_id=agency.id,
        client_id=client.id,
        agent_id=agent.id,
        contact_id=contact.id,
        channel="whatsapp",
        mode="ai",
        status="open",
    )
    db.add(conversation)
    db.flush()
    return client, agent, contact, conversation


def test_every_tool_the_table_offers_is_one_the_loop_builds(db_session):
    """A row in the table promises the editor an insertable tool. Honour it."""
    client, agent, contact, conversation = make_client(db_session)
    # The resource tool only builds when the prompt cites a resource, and a link
    # needs no bucket, so one is enough to open that gate.
    db_session.add(ClientResource(
        agency_id=client.agency_id, client_id=client.id, kind="link",
        name="Book online", url="https://acme.test/book",
    ))
    db_session.flush()

    for name, token, _aliases in TOOLS:
        agent.instructions = f"{token}\n[Recurso: Book online]"
        specs = build_commercial_tools(
            db_session, client, conversation, agent, contact, [name], CommercialEffects()
        )
        built = [spec.name for spec in specs]
        assert name in built, f"{token} is offered by the editor but the loop never builds it"


def test_every_gate_the_loop_opens_is_listed_in_the_table():
    """And the other direction: a gate nobody listed is a tool nobody offers."""
    gates = set(_GATE_RE.findall(_COMMERCIAL_SOURCE)) | set(_ALIAS_GATE_RE.findall(_COMMERCIAL_SOURCE))
    catalog = {name for _name, _token, aliases in TOOLS for name in (_name, *aliases)}
    # ``enviar_recurso`` opens its gate by name out of its own module instead of
    # from a literal, so the commercial gates are the ones compared here.
    assert gates == catalog - set(RESOURCE_TOOL_NAMES), (
        f"only in gates {gates - catalog}, only in the table {catalog - gates - set(RESOURCE_TOOL_NAMES)}"
    )
    assert set(RESOURCE_TOOL_NAMES) <= catalog, "the resource tool is missing from the table"


def test_every_block_in_the_table_has_a_builder():
    """A block with no builder is cited, recognised and offered, and injects nothing."""
    wired = set(crm_prompt_hydrator._BLOCK_BUILDERS)
    declared = {block.key for block in BLOCKS}
    assert declared == wired, f"blocks without a builder: {declared - wired}, builders without a block: {wired - declared}"


def test_blocks_land_in_the_order_the_table_declares(db_session):
    assert [key for key in crm_prompt_hydrator._BLOCK_BUILDERS] == [block.key for block in BLOCKS]

    client, _agent, contact, conversation = make_client(db_session)
    prompt = "\n".join(block.token for block in reversed(BLOCKS))
    hydrated = crm_prompt_hydrator.hydrate_commercial_prompt(
        db_session, client, conversation, prompt, contact=contact
    )
    injected = hydrated.split(prompt_variables.DYNAMIC_DATA_HEADER, 1)[1]
    headers = [line for line in injected.splitlines() if line.startswith("[") and line.endswith("]")]
    assert headers == [block.token for block in BLOCKS]


def test_a_block_is_injected_for_its_marker_and_for_each_alias(db_session):
    client, _agent, contact, conversation = make_client(db_session)
    for block in BLOCKS:
        for marker in (block.token, *block.aliases):
            hydrated = crm_prompt_hydrator.hydrate_commercial_prompt(
                db_session, client, conversation, f"Instrucciones.\n{marker}\n", contact=contact
            )
            assert hydrated != f"Instrucciones.\n{marker}\n", f"{marker} cited nothing"
            assert block.token in hydrated, f"{marker} did not inject the block headed {block.token}"


def test_only_cited_blocks_are_injected(db_session):
    client, _agent, contact, conversation = make_client(db_session)
    prompt = "Sin variables de contexto aqui."
    assert crm_prompt_hydrator.hydrate_commercial_prompt(
        db_session, client, conversation, prompt, contact=contact
    ) == prompt

    hydrated = crm_prompt_hydrator.hydrate_commercial_prompt(
        db_session, client, conversation, f"{BLOCKS[0].token} y {BLOCKS[3].token}", contact=contact
    )
    assert BLOCKS[0].token in hydrated and BLOCKS[3].token in hydrated
    for block in BLOCKS[1:3] + BLOCKS[4:]:
        assert block.token not in hydrated, f"{block.token} was not cited"


def test_a_block_marker_matches_however_the_prompt_spells_it(db_session):
    """A marker is written by hand, so casing and accents cannot decide it."""
    client, _agent, contact, conversation = make_client(db_session)
    catalog = BLOCKS[2]
    for spelling in (catalog.token, catalog.token.lower(), catalog.aliases[0], catalog.aliases[0].lower()):
        assert cited_blocks(spelling) == {catalog.key}
    hydrated = crm_prompt_hydrator.hydrate_commercial_prompt(
        db_session, client, conversation, catalog.aliases[0].lower(), contact=contact
    )
    assert catalog.token in hydrated


def test_a_tool_is_reported_the_way_the_prompt_wrote_it():
    """The tool loop shows the model the name the prompt knows, so a citation is
    never quietly renamed. The panel maps it with canonical_tool instead."""
    prompt = "[Herramienta: move_pipeline_stage] y [Herramienta: MOVE_LEAD_STAGE]"
    assert declared_tools(prompt) == ["move_pipeline_stage", "move_lead_stage"]
    assert canonical_tool("move_pipeline_stage") == "move_lead_stage"
    assert canonical_tool("MOVE_LEAD_STAGE") == "move_lead_stage"
    assert canonical_tool("cancel_appointment") is None


def test_declared_tools_keeps_order_and_drops_repeats():
    prompt = "[Herramienta: add_lead_tag] [herramienta: ADD_LEAD_TAG] [Herramienta: stay_silent]"
    assert declared_tools(prompt) == ["add_lead_tag", "stay_silent"]
    assert declared_tools(None) == []


def test_the_catalog_is_shaped_for_the_editor_and_carries_no_prose():
    """Descriptions are screen copy and live in the web dictionaries; a table
    that answered with Spanish text would put it in an English panel."""
    rows = public_catalog()
    kinds = {row["kind"] for row in rows}
    assert kinds == {"tool", "block", "stage", "resource", "field", "control"}
    for row in rows:
        assert set(row) <= {"kind", "value", "token", "template", "aliases", "picker"}
        assert row.get("token") or row.get("template"), f"{row} is neither a token nor a template"
        assert not row.get("token") or row["token"].startswith("[")
        assert not row.get("template") or row["template"].startswith("[")
    block_keys = {row["value"] for row in rows if row["kind"] == "block"}
    assert block_keys == {block.key for block in BLOCKS}
    # One tool brings a chooser of its own, so the panel is not told which one.
    assert [row["value"] for row in rows if row.get("picker")] == [RESOURCE_TOOL_NAMES[0]]


def test_the_silence_sentinel_is_a_variable_the_editor_offers():
    """A prompt may ask for silence by name; it used to have to be typed by hand."""
    silence = [row for row in public_catalog() if row["kind"] == "control"]
    assert [row["token"] for row in silence] == [SILENCE_TOKEN]


def test_a_variable_added_here_reaches_the_editor_without_touching_it():
    """The point of the table: the panel reads it, so a new row is offered by
    itself. Asserted by rebuilding the catalog from a row nobody else lists."""
    extra = prompt_variables.BlockVariable("extra", "[BLOQUE DE PRUEBA]")
    try:
        prompt_variables.BLOCKS = (*BLOCKS, extra)
        assert extra.token in [row.get("token") for row in public_catalog()]
    finally:
        prompt_variables.BLOCKS = BLOCKS
    assert extra.token not in [row.get("token") for row in public_catalog()]


def _seed_prompt() -> str:
    """The prompt the seed script gives its agents, read without running it."""
    source = (
        Path(__file__).resolve().parents[3] / "scripts" / "seed_dental_marbella.py"
    ).read_text(encoding="utf-8")
    return re.search(r'ANITA_PROMPT = """(.*?)"""', source, re.DOTALL).group(1)


def test_the_editor_route_answers_with_the_table(authenticated_client):
    """What the prompt editor reads. Reference data, so it sits with the models
    and a plain session is all it asks for."""
    response = authenticated_client.get("/api/catalog/prompt-variables")
    assert response.status_code == 200, response.text
    # The response model fills the absent half of a row with null; compare what is
    # actually said, which is the point of the route.
    present = [{key: value for key, value in row.items() if value is not None} for row in response.json()]
    assert present == [{key: value for key, value in row.items() if value is not None} for row in public_catalog()]


def _known_shapes() -> tuple[set[str], list[re.Pattern[str]]]:
    """What counts as a known citation: a marker, an alias, or a template's shape."""
    rows = public_catalog()
    exact = {fold(row["token"]) for row in rows if row.get("token")}
    exact |= {fold(alias) for row in rows for alias in row.get("aliases", [])}
    templates = []
    for row in rows:
        if not row.get("template"):
            continue
        # ``[Etapa: {name}]`` becomes a pattern that accepts any name, since the
        # stages are the client's own and the table cannot know them.
        head, _, tail = row["template"].partition("{name}")
        templates.append(
            re.compile("^" + re.escape(fold(head)) + r"[^\]]+" + re.escape(fold(tail)) + "$")
        )
    return exact, templates


def test_a_block_cited_without_any_tool_is_still_injected(db_session):
    """The bug this table came out of: a prompt can ask for the catalogue and
    nothing else, and the citation used to be a dead letter.

    It matters that the tool set stays empty and ``is_commercial`` stays false:
    a prompt that cites no tool asked for no tools, and saying otherwise would
    take away the channel's own context, escalation rules and pipeline.
    """
    client, agent, contact, conversation = make_client(db_session, instructions=BLOCKS[2].token)
    assert cited_blocks(agent.instructions) == {"catalog"}, cited_blocks(agent.instructions)
    assert agent.client is not None
    result = crm_prompt_hydrator.build_agent_context(
        db_session, agent, conversation, build_system_prompt(agent, ""), "whatsapp", contact=contact
    )
    assert BLOCKS[2].token in result.system_content
    assert "Dental Marbella" in result.system_content
    assert result.extra_specs == []
    assert result.effects is None
    assert result.is_commercial is False


def test_a_prompt_with_blocks_and_tools_still_gets_both(db_session):
    client, agent, contact, conversation = make_client(db_session)
    agent.instructions = f"{BLOCKS[2].token}\n[Herramienta: check_calendar_availability]"
    db_session.flush()
    result = crm_prompt_hydrator.build_agent_context(
        db_session, agent, conversation, build_system_prompt(agent, ""), "whatsapp", contact=contact
    )
    assert BLOCKS[2].token in result.system_content
    assert [spec.name for spec in result.extra_specs] == ["check_calendar_availability"]
    assert result.is_commercial is True
    assert result.effects is not None


def test_a_prompt_with_neither_is_left_alone(db_session):
    client, agent, contact, conversation = make_client(db_session, instructions="Solo texto.")
    db_session.flush()
    base = build_system_prompt(agent, "")
    result = crm_prompt_hydrator.build_agent_context(
        db_session, agent, conversation, base, "whatsapp", contact=contact
    )
    assert result.system_content == base
    assert result.extra_specs == []
    assert result.effects is None
    assert result.is_commercial is False


def test_the_marker_a_block_is_answered_with(db_session):
    """And the header the block arrives under is the marker that cited it, so the
    model can tell which part of the prompt asked for it."""
    client, agent, contact, conversation = make_client(db_session, instructions=BLOCKS[5].token)
    db_session.flush()
    hydrated = crm_prompt_hydrator.hydrate_cited_blocks(
        db_session, agent, conversation, build_system_prompt(agent, ""), contact=contact
    )
    assert hydrated.count(BLOCKS[5].token) == 2, "the citation in the prompt and the block's own header"


def test_a_prompt_with_only_blocks_gets_them_over_whatsapp(authenticated_client, monkeypatch):
    """The channel that dropped them: WhatsApp only opened the declarative path
    when a tool was cited, so a prompt that cited a block and nothing else lost
    it, and the citation was a dead letter in the stored prompt.

    The channel's own context, escalation and pipeline have to survive that, or
    fixing this would quietly strip a live agent of what it could do.
    """
    from app.config import get_settings
    from app.services import ai as ai_service
    from app.services import whatsapp_inbound as inbound

    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Casa", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    client.post(f"/api/clients/{customer['id']}/pipeline/stages", json={"name": "Descubrimiento"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "gpt-4.1-mini",
        "name": "Bella", "instructions": BLOCKS[2].token, "is_active": True,
    }).json()
    channel = client.put(f"/api/whatsapp/channels/{customer['id']}", json={"agent_id": agent["id"]}).json()

    seen: dict = {}

    async def capture(db, agent, base_url, api_key, messages, **kwargs):
        seen["system"] = messages[0]["content"]
        seen["specs"] = kwargs.get("extra_specs")
        return ai_service.Completion(text="Hola")

    monkeypatch.setattr(inbound, "run_completion", capture)
    response = client.post(
        f"/api/internal/whatsapp/channels/{channel['id']}/inbound",
        headers={"X-Bridge-Token": get_settings().whatsapp_bridge_token},
        json={
            "external_message_id": "wamid.BLOCKS1", "remote_jid": "573001112233@s.whatsapp.net",
            "sender_name": "Maria", "text": "Hola",
        },
    )
    assert response.status_code == 200, response.text
    system = seen["system"]
    assert BLOCKS[2].token in system, "the cited block never arrived"
    assert "No hay servicios" in system, "the block arrived empty"
    assert seen["specs"], "the channel lost its own tools to the hydration"


def test_every_marker_the_table_ships_is_well_formed_and_unique():
    """What the editor matches on. A token that is not a well-formed marker, or
    that two variables share, would make the editor paint one thing as another."""
    seen: dict[str, str] = {}
    for row in public_catalog():
        markers = [row["token"], *row["aliases"]] if row.get("token") else []
        for marker in markers:
            assert marker.startswith("[") and marker.endswith("]"), f"{marker} is not a marker"
            assert "\n" not in marker and "[" not in marker[1:-1], f"{marker} cannot be matched as one marker"
            assert 2 < len(marker) <= 120, f"{marker} is not a length the editor reads"
            previous = seen.setdefault(fold(marker), row["value"])
            assert previous == row["value"], f"{marker} is claimed by both {previous} and {row['value']}"
        if row.get("template"):
            assert "{name}" in row["template"], f"{row['template']} does not say where the name goes"


def test_the_shipped_prompt_cites_only_variables_this_release_knows():
    """The prompt an installation actually runs, checked against the table.

    A marker there that the table does not know is a variable the editor would
    paint as unknown, and a tool the loop would never build.
    """
    exact, templates = _known_shapes()
    unknown = [
        span
        for span in re.findall(r"\[[^\[\]]*\]", _seed_prompt())
        if _reads_as_a_variable(span)
        and fold(span) not in exact
        and not any(pattern.match(fold(span)) for pattern in templates)
    ]
    assert unknown == [], f"the seed prompt cites variables the table does not know: {unknown}"


def test_the_shipped_prompt_leaves_its_example_bracket_text_alone():
    """The other half of the same contract. A prompt illustrates itself with
    placeholders inside the words of a sample reply ("este [Día 1]"), and the
    editor must not paint those as variables it does not know."""
    prompt = _seed_prompt()
    for example in ("[Día 1]", "[Día 2]", "[Día]"):
        assert example in prompt, f"the seed prompt no longer illustrates {example}"
        assert not _reads_as_a_variable(example), f"{example} would be painted as an unknown variable"


def _reads_as_a_variable(span: str) -> bool:
    """What the editor's scanner treats as a variable, and what it leaves alone.

    Bracketed example text is everywhere in a prompt ("este [Día 1]"), so a span
    only counts when it is named like one: a value after a colon, or a phrase in
    capitals. The editor applies the same rule; this is the contract between them.
    """
    inner = span[1:-1].strip()
    if not inner or len(span) > 122:
        return False
    if any(char in inner for char in "\"'`=<>(){}<>|\\"):
        return False
    if ":" in inner:
        return True
    return inner.upper() == inner and len(inner.split()) >= 2
