from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass


@dataclass
class PublishResult:
    command: str
    return_code: int
    stdout: str
    stderr: str


def publish_with_opencli(content: str, base_command: str = "opencli post", dry_run: bool = False) -> PublishResult:
    cmd_parts = shlex.split(base_command)
    cmd = [*cmd_parts, content]
    command_str = " ".join(shlex.quote(x) for x in cmd)
    if dry_run:
        return PublishResult(command=command_str, return_code=0, stdout="dry-run", stderr="")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return PublishResult(
        command=command_str,
        return_code=proc.returncode,
        stdout=(proc.stdout or "").strip(),
        stderr=(proc.stderr or "").strip(),
    )

