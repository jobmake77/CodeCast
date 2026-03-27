from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def run_git(repo_path: str, args: list[str]) -> str:
    cmd = ["git", *args]
    proc = subprocess.run(
        cmd,
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git command failed: {' '.join(cmd)}")
    return proc.stdout.strip()


def _git_ok(repo_path: str, args: list[str]) -> bool:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def resolve_repo_path(repo_path: str | None = None) -> str:
    if repo_path:
        return str(Path(repo_path).resolve())
    cwd_repo = run_git(".", ["rev-parse", "--show-toplevel"]).strip()
    return str(Path(cwd_repo).resolve())


def collect_commits(repo_path: str, oldrev: str | None, newrev: str | None) -> tuple[str, str, list[dict]]:
    current_head = run_git(repo_path, ["rev-parse", "HEAD"]).strip()
    final_new = newrev or current_head
    old_is_zero = bool(oldrev) and set(oldrev) == {"0"}
    old_exists = bool(oldrev) and _git_ok(repo_path, ["cat-file", "-e", f"{oldrev}^{{commit}}"])
    if oldrev and (not old_is_zero) and old_exists:
        final_old = oldrev
        log_args = [
            "log",
            "--reverse",
            "--pretty=format:%H%x1f%s%x1f%an%x1f%aI",
            f"{final_old}..{final_new}",
        ]
    else:
        final_old = oldrev or "0000000000000000000000000000000000000000"
        has_parent = _git_ok(repo_path, ["rev-parse", f"{final_new}~1"])
        if has_parent and not oldrev:
            parent = run_git(repo_path, ["rev-parse", f"{final_new}~1"]).strip()
            final_old = parent
            log_args = [
                "log",
                "--reverse",
                "--pretty=format:%H%x1f%s%x1f%an%x1f%aI",
                f"{final_old}..{final_new}",
            ]
        else:
            # First push/new branch (remote old SHA may be zeros or missing in local graph).
            # Use commit ancestry up to newrev.
            log_args = [
                "log",
                "--reverse",
                "--pretty=format:%H%x1f%s%x1f%an%x1f%aI",
                final_new,
            ]

    log_text = run_git(repo_path, log_args)
    commits: list[dict] = []
    if not log_text:
        return final_old, final_new, commits
    for line in log_text.splitlines():
        sha, subject, author, committed_at = line.split("\x1f")
        numstat = run_git(repo_path, ["show", "--numstat", "--format=", sha]).strip()
        files_changed = insertions = deletions = 0
        if numstat:
            for ns in numstat.splitlines():
                parts = ns.split("\t")
                if len(parts) < 3:
                    continue
                ins, dels = parts[0], parts[1]
                files_changed += 1
                if ins.isdigit():
                    insertions += int(ins)
                if dels.isdigit():
                    deletions += int(dels)
        commits.append(
            {
                "sha": sha,
                "subject": subject,
                "author": author,
                "committed_at": committed_at,
                "files_changed": files_changed,
                "insertions": insertions,
                "deletions": deletions,
            }
        )
    return final_old, final_new, commits


def install_post_push_hook(repo_path: str, db_path: str | None) -> str:
    hooks_dir = Path(repo_path) / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "post-push"
    project_root = Path(__file__).resolve().parents[2]
    src_path = project_root / "src"
    db_export = f'export CODECAST_DB_PATH="{db_path}"\n' if db_path else ""
    script = f"""#!/usr/bin/env bash
set -euo pipefail
{db_export}while read -r local_ref local_sha remote_ref remote_sha; do
  if command -v codecast >/dev/null 2>&1; then
    codecast collect --repo "{repo_path}" --oldrev "$remote_sha" --newrev "$local_sha"
  else
    PYTHONPATH="{src_path}" python3 -m codecast.cli {"--db-path " + db_path if db_path else ""} collect --repo "{repo_path}" --oldrev "$remote_sha" --newrev "$local_sha"
  fi
done
"""
    hook.write_text(script)
    hook.chmod(0o755)
    return str(hook)
