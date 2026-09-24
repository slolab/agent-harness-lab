"""Portable native-tool denials and the adapter-owned preparation contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ahl.config import RunConfig
    from ahl.docker import Volumes

OPERATIONS = frozenset({"websearch", "webfetch"})


@dataclass(frozen=True)
class PermissionPolicy:
    deny: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PermissionSetup:
    readonly_volumes: Volumes
    applied: frozenset[str]
    unsupported: dict[str, str]


class PermissionHandler(Protocol):
    def prepare(
        self, run_dir: Path, config: RunConfig, policy: PermissionPolicy
    ) -> PermissionSetup: ...


def parse_permissions(raw: Any) -> PermissionPolicy:
    from ahl.config import ConfigError

    if not isinstance(raw, dict) or set(raw) - {"deny"}:
        raise ConfigError("permissions must be a mapping with only the 'deny' key")
    deny = raw.get("deny", [])
    if not isinstance(deny, list) or any(
        not isinstance(op, str) or op not in OPERATIONS for op in deny
    ):
        raise ConfigError(
            "permissions.deny must be a list containing only websearch or webfetch"
        )
    return PermissionPolicy(frozenset(deny))


def validate_setup(policy: PermissionPolicy, setup: PermissionSetup) -> None:
    """Never record missing, overlapping, or invented enforcement as success."""
    from ahl.config import ConfigError

    applied, unsupported = set(setup.applied), set(setup.unsupported)
    if (
        applied & unsupported
        or applied | unsupported != policy.deny
        or any(
            not isinstance(reason, str) or not reason.strip()
            for reason in setup.unsupported.values()
        )
    ):
        raise ConfigError(
            "Invalid permission handler result: account for each requested operation exactly once"
        )


class UnsupportedPermissions:
    def prepare(
        self, run_dir: Path, config: RunConfig, policy: PermissionPolicy
    ) -> PermissionSetup:
        return PermissionSetup(
            [],
            frozenset(),
            {
                op: f"harness '{config.harness.name}' has no native permission mapping"
                for op in sorted(policy.deny)
            },
        )
