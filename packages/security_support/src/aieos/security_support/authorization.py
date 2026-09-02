"""Authorization checks executed by the accountable command target."""

from aieos.contracts import AuthorizationContext


class AuthorizationFailure(ValueError):
    """Raised when verified context does not authorize the target operation."""


class ScopeAuthorizer:
    """Enforce exact Tenant/Workspace scope and explicit permission."""

    def __init__(
        self,
        *,
        active_policy_versions: set[tuple[str, str, str, str]] | None = None,
    ) -> None:
        self._active_policy_versions = (
            None if active_policy_versions is None else set(active_policy_versions)
        )
        self._revoked_policy_versions: set[tuple[str, str, str, str]] = set()

    @staticmethod
    def _policy_key(context: AuthorizationContext) -> tuple[str, str, str, str]:
        return (
            context.tenant_id,
            context.workspace_id,
            context.policy_id,
            context.policy_version_id,
        )

    def revoke(self, context: AuthorizationContext) -> None:
        """Revoke the exact scoped policy version without rebinding stored snapshots."""
        self._revoked_policy_versions.add(self._policy_key(context))

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
        if permission == "ai.invoke":
            policy_key = self._policy_key(context)
            if policy_key in self._revoked_policy_versions:
                raise AuthorizationFailure("authorization policy version is revoked")
            if (
                self._active_policy_versions is not None
                and policy_key not in self._active_policy_versions
            ):
                raise AuthorizationFailure(
                    "authorization policy version is unknown or incompatible"
                )
        if permission not in context.permissions:
            raise AuthorizationFailure(f"missing required permission: {permission}")


__all__ = ("AuthorizationFailure", "ScopeAuthorizer")
