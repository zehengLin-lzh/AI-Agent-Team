"""Sandboxed code execution with resource limits."""
import asyncio
from pathlib import Path


class SandboxExecutor:
    def __init__(self, workspace: Path, timeout: int = 30):
        self.workspace = workspace.resolve()
        self.timeout = timeout

    async def execute(self, command: str) -> tuple[str, str, int]:
        """Execute a command in a restricted subprocess.
        Returns (stdout, stderr, returncode)."""
        env = {
            "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(self.workspace),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "en_US.UTF-8",
        }

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(self.workspace),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout,
            )
            return (
                stdout.decode(errors="replace")[:10000],  # Cap output at 10KB
                stderr.decode(errors="replace")[:10000],
                proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return "", "Execution timed out", -1
        except Exception as e:
            return "", f"Execution error: {str(e)}", -1
