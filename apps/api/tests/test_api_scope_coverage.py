"""What the scope guards have to hold to, read from the application's own
route table.

A catalogue is only as good as its coverage. A route that declares nothing is
closed to tokens, which is safe; a *read* scope on a route that writes is a
leak. These checks walk ``app.routes`` instead of trusting the code to stay
right, so a new route has to make a deliberate choice rather than inherit one.
"""

from conftest import customer_conversation

from app.api_scopes import DESCRIPTIONS, PRESETS
from app.deps import _declared_scopes
from app.main import app

# Areas that are a person's business or belong to a credential model of their
# own. A token is refused everywhere these live, on purpose.
CLOSED_PREFIXES = (
    "/api/auth",       # signing in, first-run setup, the session itself
    "/api/agency",     # the agency's identity and logo
    "/api/providers",  # the AI keys
    "/api/catalog",    # reference data the panel reads
    "/api/industries",
    "/api/dashboard",  # summary views, still to be given resources of their own
    "/api/messaging",  # provider webhook housekeeping
    "/api/portal",     # the portal has its own credential model
    "/api/mobile",     # and so does the mobile app
    "/api/widget",     # the embeddable widget is public
    "/api/public",     # webhooks and public assets
    "/api/internal",   # the driver-agnostic internal API
)

# Individual routes inside otherwise-open areas that stay closed.
CLOSED_ROUTES = (
    ("POST", "/api/conversations"),                      # the playground is a panel feature
    ("DELETE", "/api/conversations/{conversation_id}"),  # and only deletes playground threads
)


def _routes():
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = set(getattr(route, "methods", set()) or set())
        if not path.startswith("/api") or methods <= {"HEAD", "OPTIONS"}:
            continue
        yield route, path, methods


def test_a_read_scope_never_guards_a_write():
    """When every scope a route demands ends in ``.read``, the route may only
    read. This is the invariant that keeps a read-only credential from
    changing anything."""
    offenders = []
    for route, path, methods in _routes():
        scopes = _declared_scopes(route)
        if not scopes or not all(scope.endswith(".read") for scope in scopes):
            continue
        if methods - {"GET", "HEAD"}:
            offenders.append(f"{sorted(methods)} {path} demands {list(scopes)}")
    assert not offenders, "A read scope is guarding a write: " + "; ".join(offenders)


def test_every_route_says_who_may_call_it():
    """No route under /api is silently open to tokens because nobody thought
    about it."""
    offenders = []
    for route, path, methods in _routes():
        if _declared_scopes(route):
            continue
        if path.startswith(CLOSED_PREFIXES):
            continue
        if any(path == closed and method in methods for method, closed in CLOSED_ROUTES):
            continue
        offenders.append(f"{sorted(methods)} {path}")
    assert not offenders, (
        "Neither scoped nor listed as deliberately closed, so annotate these with "
        "Depends(require(...)) or add their prefix to CLOSED_PREFIXES: " + "; ".join(offenders)
    )


def test_every_declared_scope_exists_and_is_described():
    """A scope that is not in the catalogue could not be granted by anybody."""
    undeclared = set()
    for route, path, methods in _routes():
        for scope in _declared_scopes(route) or ():
            if scope not in DESCRIPTIONS:
                undeclared.add(scope)
    assert not undeclared, f"Scopes demanded but not in the catalogue: {sorted(undeclared)}"


def test_the_presets_only_offer_real_scopes():
    for name, keys in PRESETS.items():
        assert keys, f"The {name} preset is empty"
        unknown = sorted(key for key in keys if key not in DESCRIPTIONS)
        assert not unknown, f"The {name} preset offers unknown scopes: {unknown}"


def _issued(client, preset: str) -> dict:
    integration = client.post("/api/integrations", json={"name": f"Key {preset}", "preset": preset}).json()
    return client.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 30}).json()


def _endpoint(client, method: str, path: str, headers: dict, payload: dict | None = None):
    return client.request(method, path, headers=headers, json=payload)


def test_a_read_only_credential_reads_every_area(authenticated_client):
    """The whole surface the catalogue covers, on one read-only token."""
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    # The web chat has to exist before it can be read; the seed is the panel's
    # own way in, the reads below are what the token is being judged on.
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openrouter", "model": "openai/gpt-5.6-luna",
              "name": "Vera", "instructions": "", "personality": "", "is_active": True},
    ).json()
    configured = client.put(
        f"/api/webchat/channels/{customer['id']}",
        json={"agent_id": agent["id"], "is_enabled": True, "greeting": "Hola", "color": "#075985", "position": "right"},
    )
    assert configured.status_code == 200, configured.text
    headers = {"Authorization": f"Bearer {_issued(client, 'read_only')['token']}"}

    reads = [
        ("GET", "/api/clients"),
        ("GET", f"/api/clients/{customer['id']}"),
        ("GET", "/api/agents"),
        ("GET", "/api/conversations"),
        ("GET", "/api/conversations/inbox"),
        ("GET", "/api/reports/filters"),
        ("GET", f"/api/clients/{customer['id']}/pipeline/board"),
        ("GET", f"/api/clients/{customer['id']}/pipeline/stages"),
        ("GET", f"/api/clients/{customer['id']}/teams"),
        ("GET", f"/api/clients/{customer['id']}/members"),
        ("GET", f"/api/clients/{customer['id']}/contact-tags"),
        ("GET", f"/api/clients/{customer['id']}/calendar"),
        ("GET", f"/api/whatsapp/clients/{customer['id']}/channels"),
        ("GET", f"/api/webchat/channels/{customer['id']}"),
    ]
    for method, path in reads:
        response = _endpoint(client, method, path, headers)
        assert response.status_code == 200, f"{method} {path} answered {response.status_code}: {response.text}"


def test_a_read_only_credential_writes_nothing(authenticated_client):
    """The same token, against every write the catalogue covers: each refusal
    names the scope it would have needed."""
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    headers = {"Authorization": f"Bearer {_issued(client, 'read_only')['token']}"}

    writes = [
        ("POST", "/api/clients", {"name": "Nope", "is_active": True}),
        ("PATCH", f"/api/clients/{customer['id']}", {"name": "Renamed"}),
        ("POST", f"/api/clients/{customer['id']}/teams", {"name": "Sales"}),
        ("POST", f"/api/clients/{customer['id']}/contact-tags", {"name": "Vip", "color": "#2f6df0"}),
        ("POST", f"/api/clients/{customer['id']}/pipeline/stages", {"name": "Lead"}),
        ("POST", "/api/agents", {"client_id": customer["id"], "provider": "openrouter", "model": "openai/gpt-5.6-luna",
                                 "name": "Vera", "instructions": "", "personality": "", "is_active": True}),
        ("DELETE", f"/api/clients/{customer['id']}", None),
    ]
    for method, path, payload in writes:
        response = _endpoint(client, method, path, headers, payload)
        assert response.status_code == 403, f"{method} {path} answered {response.status_code}: {response.text}"
        assert "does not hold" in response.json()["detail"]


def test_the_operator_preset_works_the_inbox(authenticated_client):
    """A reply scope does what its description says: take over and answer."""
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openrouter", "model": "openai/gpt-5.6-luna",
              "name": "Vera", "instructions": "", "personality": "", "is_active": True},
    ).json()
    conversation = customer_conversation(client, agent["id"])

    reader = {"Authorization": f"Bearer {_issued(client, 'read_only')['token']}"}
    operator = {"Authorization": f"Bearer {_issued(client, 'operator')['token']}"}

    takeover = {"mode": "human"}
    assert _endpoint(client, "PATCH", f"/api/conversations/{conversation['id']}/mode", reader, takeover).status_code == 403
    assert _endpoint(client, "PATCH", f"/api/conversations/{conversation['id']}/mode", operator, takeover).status_code == 200

    reply = {"content": "Hola, te atiendo yo"}
    refused = _endpoint(client, "POST", f"/api/conversations/{conversation['id']}/reply", reader, reply)
    assert refused.status_code == 403
    assert "inbox.reply" in refused.json()["detail"]
    assert _endpoint(client, "POST", f"/api/conversations/{conversation['id']}/reply", operator, reply).status_code == 200
