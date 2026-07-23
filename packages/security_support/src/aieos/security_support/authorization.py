"""Authorization checks executed by the accountable command target."""

from aieos.contracts import AuthorizationContext


class AuthorizationFailure(ValueError):
    """Raised when verified context does not authorize the target operation."""


class ScopeAuthorizer:
    """Enforce exact Tenant/Workspace scope and explicit permission."""

    def require(
        self,
        context: AuthorizationContext,
        *,
        permission: str,
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        if context.tenant_id != tenant_id or context.workspace_id != workspace_id:
            raise AuthorizationFailure("authorization scope does not match target scope")
        if permission not in context.permissions:
            raise AuthorizationFailure(f"missing required permission: {permission}")


__all__ = ("AuthorizationFailure", "ScopeAuthorizer")
