"""Every platform route asks for the platform identity, except the few
deliberately public endpoints (the login, and the invitation flows an
invitee opens without a session). Read from the route table, like the
scope-coverage suite, so a new platform route cannot forget the door."""

from app.deps import get_current_platform_admin
from app.main import app

PUBLIC_PLATFORM_ROUTES = {
    "/api/platform/auth/login",
    "/api/platform/invitations/{token}",
    "/api/platform/invitations/{token}/accept",
}


def _requires_platform(route) -> bool:
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    stack = [dependant]
    visited = 0
    while stack and visited < 200:
        node = stack.pop()
        visited += 1
        if getattr(node, "call", None) is get_current_platform_admin:
            return True
        stack.extend(getattr(node, "dependencies", ()) or ())
    return False


def test_every_platform_route_is_behind_the_platform_door():
    offenders = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = set(getattr(route, "methods", set()) or set())
        if not path.startswith("/api/platform") or methods <= {"HEAD", "OPTIONS"}:
            continue
        if path in PUBLIC_PLATFORM_ROUTES:
            continue
        if not _requires_platform(route):
            offenders.append(f"{sorted(methods)} {path}")
    assert not offenders, (
        "Add Depends(get_current_platform_admin) or list the route as public: " + "; ".join(offenders)
    )
