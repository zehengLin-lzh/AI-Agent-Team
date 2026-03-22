"""Workspace guard — validates file paths are within allowed boundaries."""
from pathlib import Path


class SecurityError(Exception):
    """Raised when a security check fails."""
    pass


BLOCKED_FILENAMES = {
    ".env", ".env.local", ".env.production",
    "id_rsa", "id_ed25519", "id_ecdsa",
    ".secrets", "credentials.json", "service-account.json",
    ".npmrc", ".pypirc",
}

BLOCKED_DIRECTORIES = {
    ".ssh", ".gnupg", ".aws", ".config",
}


class WorkspaceGuard:
    def __init__(self, allowed_roots: list[Path]):
        self.allowed_roots = [p.resolve() for p in allowed_roots]

    def is_path_allowed(self, target: Path) -> bool:
        """Check if target path is inside any allowed root."""
        resolved = target.resolve()
        return any(self._is_inside(root, resolved) for root in self.allowed_roots)

    def _is_inside(self, base: Path, candidate: Path) -> bool:
        try:
            candidate.relative_to(base)
            return True
        except ValueError:
            return False

    def validate_read(self, target: Path) -> None:
        """Raise SecurityError if read is not allowed."""
        if not self.is_path_allowed(target):
            raise SecurityError(f"Read blocked: {target} is outside allowed workspace")

    def validate_write(self, target: Path) -> None:
        """Raise SecurityError if write is not allowed."""
        resolved = target.resolve()
        if not self.is_path_allowed(resolved):
            raise SecurityError(f"Write blocked: {target} is outside allowed workspace")
        if target.name in BLOCKED_FILENAMES:
            raise SecurityError(f"Write blocked: {target.name} is a sensitive file")
        for parent in resolved.parents:
            if parent.name in BLOCKED_DIRECTORIES and parent != resolved:
                raise SecurityError(f"Write blocked: path includes sensitive directory {parent.name}")
