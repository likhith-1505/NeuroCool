"""A minimal permission boundary — not an authentication system (the
objective explicitly rules that out for this phase). Every tool declares
which permission it needs; every tool call goes through `require()` before
it runs. Today's only Principal (DEFAULT_PRINCIPAL) always holds both
permissions, since there is no login/session concept yet — but because the
check is already wired into every call site, adding real RBAC later is
"construct a different Principal per request", not "go find every place
that needs a check and add one".
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Permission(str, enum.Enum):
    READ_ONLY = "read_only"
    OPERATE = "operate"


@dataclass(frozen=True)
class Principal:
    """Who is asking. `identifier` is a free-form label for audit logs
    (see app.models.audit_log) — there's no user/session system yet, so
    it defaults to a fixed local-dev label rather than inventing a fake
    user id.
    """

    identifier: str = "local-dev"
    permissions: frozenset[Permission] = field(
        default_factory=lambda: frozenset({Permission.READ_ONLY, Permission.OPERATE})
    )

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions


DEFAULT_PRINCIPAL = Principal()


class PermissionDenied(Exception):
    """Raised by require() — callers turn this into a tool-call failure
    (fed back to the model) or an HTTP 403, never a silent no-op.
    """


def require(principal: Principal, permission: Permission) -> None:
    if not principal.has(permission):
        raise PermissionDenied(f"'{principal.identifier}' is missing required permission: {permission.value}")
