from __future__ import annotations

import argparse
import curses
import os
import shutil
import shlex
import sys
import textwrap
from typing import Iterable

from . import __version__
from .git_ops import GitError, collect_commits, install_post_push_hook, resolve_repo_path
from .publisher import publish_with_opencli
from .storage import (
    STATUS_ARCHIVED,
    STATUS_FAILED,
    STATUS_PENDING,
    STYLE_VALUES,
    aggregate_ready_events,
    connect,
    create_push_event,
    ensure_repo,
    find_publishable_drafts,
    find_repo_ids,
    get_draft,
    get_draft_event_ids,
    get_repo_settings,
    init_db,
    list_config,
    list_drafts,
    list_recent_publish_activity,
    list_publish_logs,
    count_drafts,
    get_config,
    mark_publish_result,
    render_content,
    rerender_draft,
    set_config,
    update_repo_settings,
    build_summary,
)


def _print(msg: str) -> None:
    sys.stdout.write(msg + "\n")


def cmd_init(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    _print(f"Initialized database at {conn.execute('PRAGMA database_list').fetchone()['file']}")
    return 0


def cmd_settings_set(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    repo_path = resolve_repo_path(args.repo)
    repo_id = ensure_repo(conn, repo_path)
    style = args.default_style if args.default_style else None
    update_repo_settings(
        conn,
        repo_id=repo_id,
        every_n_pushes=args.every_n_pushes,
        publish_enabled=args.publish_enabled,
        default_style=style,
    )
    settings = get_repo_settings(conn, repo_id)
    _print(
        f"Updated settings for {repo_path}: every_n_pushes={settings.every_n_pushes}, "
        f"publish_enabled={settings.publish_enabled}, default_style={settings.default_style}, "
        f"require_confirm={settings.require_confirm}"
    )
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    repo_path = resolve_repo_path(args.repo)
    repo_id = ensure_repo(conn, repo_path)
    try:
        oldrev, newrev, commits = collect_commits(repo_path, args.oldrev, args.newrev)
    except GitError as exc:
        _print(f"collect failed: {exc}")
        return 1
    if not commits:
        _print("No new commits found in provided range; nothing collected.")
        return 0
    event_id = create_push_event(conn, repo_id=repo_id, oldrev=oldrev, newrev=newrev, commits=commits)
    created_drafts = aggregate_ready_events(conn, repo_id)
    _print(f"Collected push_event={event_id} with {len(commits)} commits for {repo_path}")
    if created_drafts:
        _print(f"Created drafts: {', '.join(str(i) for i in created_drafts)}")
    else:
        settings = get_repo_settings(conn, repo_id)
        _print(f"No draft yet. Waiting for every_n_pushes={settings.every_n_pushes} threshold.")
    return 0


def _format_draft_row(row) -> str:
    repo = row["repo_name"] or "multi-repo"
    return f"[{row['id']}] {row['status']} | {repo} | {row['style']} | {row['title']}"


def cmd_drafts_list(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    status = None if args.all else STATUS_PENDING
    rows = list_drafts(conn, status=status)
    if not rows:
        _print("No drafts found.")
        return 0
    for row in rows:
        _print(_format_draft_row(row))
    return 0


def _rerender_and_print(conn, draft_id: int, style: str | None) -> int:
    draft = get_draft(conn, draft_id)
    if not draft:
        _print(f"Draft {draft_id} not found.")
        return 1
    if style:
        rerender_draft(conn, draft_id, style)
        draft = get_draft(conn, draft_id)
    assert draft is not None
    _print(_format_draft_row(draft))
    _print("")
    _print(draft["content"])
    return 0


def cmd_drafts_render(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    if args.draft:
        return _rerender_and_print(conn, args.draft, args.style)
    rows = list_drafts(conn, status=STATUS_PENDING)
    if not rows:
        _print("No pending drafts.")
        return 0
    for row in rows:
        _rerender_and_print(conn, int(row["id"]), args.style)
        _print("-" * 60)
    return 0


def _publish_one(conn, draft_id: int, opencli_cmd: str, dry_run: bool) -> int:
    draft = get_draft(conn, draft_id)
    if not draft:
        _print(f"Draft {draft_id} not found.")
        return 1
    if draft["status"] == STATUS_ARCHIVED:
        _print(f"Draft {draft_id} already archived/published. Skipping.")
        return 0
    result = publish_with_opencli(draft["content"], base_command=opencli_cmd, dry_run=dry_run)
    mark_publish_result(
        conn,
        draft_id=draft_id,
        command=result.command,
        return_code=result.return_code,
        stdout=result.stdout,
        stderr=result.stderr,
        dry_run=dry_run,
    )
    if result.return_code == 0:
        action = "Dry-run simulated" if dry_run else "Published and archived"
        _print(f"{action} draft {draft_id}")
        return 0
    _print(f"Failed publishing draft {draft_id}: {result.stderr or result.stdout}")
    return 1


def _merge_draft_content(conn, draft_ids: Iterable[int], style: str) -> tuple[str, str]:
    parts: list[str] = []
    event_ids: list[int] = []
    for draft_id in draft_ids:
        draft = get_draft(conn, int(draft_id))
        if not draft:
            continue
        repo_name = draft["repo_name"] or "repo"
        parts.append(f"[{repo_name}]")
        parts.append(draft["content"])
        parts.append("")
        event_ids.extend(get_draft_event_ids(conn, int(draft_id)))
    summary = build_summary(conn, event_ids)
    title, top = render_content("multi-repo", summary, style)
    merged = top + "\n\n" + "\n".join(parts).strip()
    return title, merged


def cmd_publish(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    configured_cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post")
    opencli_cmd = args.opencli_cmd or configured_cmd or "opencli twitter post"
    if args.draft is not None:
        return _publish_one(conn, args.draft, opencli_cmd, args.dry_run)

    if not args.repos:
        _print("Provide either --draft <id> or --repos <a,b>.")
        return 1
    repo_paths = [p.strip() for p in args.repos.split(",") if p.strip()]
    repo_ids = find_repo_ids(conn, repo_paths)
    drafts = find_publishable_drafts(conn, repo_ids)
    if not drafts:
        _print("No pending/failed drafts found for selected repos.")
        return 0
    if args.mode == "separate":
        rc = 0
        for draft in drafts:
            if _publish_one(conn, int(draft["id"]), opencli_cmd, args.dry_run) != 0:
                rc = 1
        return rc

    style = args.style or "formal"
    merged_title, merged_content = _merge_draft_content(conn, [int(d["id"]) for d in drafts], style)
    payload = f"{merged_title}\n\n{merged_content}"
    result = publish_with_opencli(payload, base_command=opencli_cmd, dry_run=args.dry_run)
    rc = 0 if result.return_code == 0 else 1
    for draft in drafts:
        mark_publish_result(
            conn,
            draft_id=int(draft["id"]),
            command=result.command,
            return_code=result.return_code,
            stdout=result.stdout,
            stderr=result.stderr,
            dry_run=args.dry_run,
        )
    if result.return_code == 0:
        action = "Dry-run simulated" if args.dry_run else "Published merged update and archived related drafts"
        _print(action)
    else:
        _print(f"Merged publish failed: {result.stderr or result.stdout}")
    return rc


def cmd_install_hook(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    repo_path = resolve_repo_path(args.repo)
    ensure_repo(conn, repo_path)
    hook_path = install_post_push_hook(repo_path, args.db_path)
    _print(f"Installed post-push hook at {hook_path}")
    return 0


def _run_quick_setup(conn, db_path: str | None, repo_path: str | None) -> tuple[bool, str]:
    notes: list[str] = []
    configured = get_config(conn, "publish.opencli_cmd")
    if not configured:
        set_config(conn, "publish.opencli_cmd", "opencli twitter post")
        configured = "opencli twitter post"
        notes.append("set publish.opencli_cmd=opencli twitter post")
    else:
        notes.append("kept existing publish.opencli_cmd")

    cmd_bin = ""
    try:
        cmd_bin = shlex.split(configured)[0] if configured else "opencli"
    except ValueError:
        cmd_bin = "opencli"
    if cmd_bin and not shutil.which(cmd_bin):
        notes.append(f"warning: '{cmd_bin}' not found in PATH")

    repo_target = repo_path
    if not repo_target:
        try:
            repo_target = resolve_repo_path(".")
        except GitError:
            repo_target = None
    if not repo_target:
        notes.append("repo not detected; skip hook install")
        return True, "; ".join(notes)
    try:
        repo_target = resolve_repo_path(repo_target)
        ensure_repo(conn, repo_target)
        hook_path = install_post_push_hook(repo_target, db_path)
        notes.append(f"installed hook: {hook_path}")
    except Exception as exc:  # pragma: no cover - defensive runtime feedback
        return False, f"setup failed while installing hook: {exc}"
    return True, "; ".join(notes)


def cmd_setup(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    ok, msg = _run_quick_setup(conn, args.db_path, args.repo)
    _print(f"Quick setup {'completed' if ok else 'failed'}: {msg}")
    return 0 if ok else 1


def cmd_onboarding_status(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    done = get_config(conn, "ui.onboarding_done", "0") == "1"
    _print(f"onboarding_done={'1' if done else '0'}")
    return 0


def cmd_onboarding_reset(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    set_config(conn, "ui.onboarding_done", "0")
    _print("Onboarding has been reset. Next 'codecast' launch will show onboarding.")
    return 0


def cmd_onboarding_complete(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    set_config(conn, "ui.onboarding_done", "1")
    _print("Onboarding marked as completed.")
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    set_config(conn, args.key, args.value)
    _print(f"Config saved: {args.key}={args.value}")
    return 0


def cmd_config_get(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    if args.key:
        value = get_config(conn, args.key)
        if value is None:
            _print(f"Config key not found: {args.key}")
            return 1
        _print(f"{args.key}={value}")
        return 0
    rows = list_config(conn)
    if not rows:
        _print("No config found.")
        return 0
    for row in rows:
        _print(f"{row['key']}={row['value']}")
    return 0


def _ui_help() -> None:
    _print("CodeCast UI commands:")
    _print("  /pending                       List pending drafts")
    _print("  /all                           List all drafts")
    _print("  /view <draft_id> [style]       Render a draft (optional style override)")
    _print("  /post <draft_id> [--dry-run]   Publish one draft")
    _print("  /post latest [--dry-run]       Publish latest pending/failed draft")
    _print("  /repos <a,b> <mode> [--dry-run] Publish by repos, mode=merged|separate")
    _print("  /config show                   Show global config")
    _print("  /config set <key> <value>      Set global config")
    _print("  /help                          Show this help")
    _print("  /exit                          Exit UI")


def _latest_publishable_draft_id(conn) -> int | None:
    row = conn.execute(
        "SELECT id FROM drafts WHERE status IN (?, ?) ORDER BY id DESC LIMIT 1",
        (STATUS_PENDING, STATUS_FAILED),
    ).fetchone()
    return int(row["id"]) if row else None


def _latest_draft_id(conn) -> int | None:
    row = conn.execute("SELECT id FROM drafts ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else None


def _handle_slash_command(conn, raw: str, printer=_print) -> bool:
    if not raw.startswith("/"):
        printer("Use slash commands, e.g. /pending or /help")
        return True
    parts = shlex.split(raw)
    cmd = parts[0].lower()
    if cmd in ("/exit", "/quit"):
        return False
    if cmd == "/help":
        printer("CodeCast UI commands:")
        printer("  /pending                       List pending drafts")
        printer("  /all                           List all drafts")
        printer("  /view <draft_id> [style]       Render a draft (optional style override)")
        printer("  /post <draft_id> [--dry-run]   Publish one draft")
        printer("  /post latest [--dry-run]       Publish latest pending/failed draft")
        printer("  /repos <a,b> <mode> [--dry-run] Publish by repos, mode=merged|separate")
        printer("  /config show                   Show global config")
        printer("  /config set <key> <value>      Set global config")
        printer("  /help                          Show this help")
        printer("  /exit                          Exit UI")
        return True
    if cmd == "/pending":
        rows = list_drafts(conn, status=STATUS_PENDING)
        if not rows:
            printer("No pending drafts.")
        else:
            for row in rows:
                printer(_format_draft_row(row))
        return True
    if cmd == "/all":
        rows = list_drafts(conn, status=None)
        if not rows:
            printer("No drafts found.")
        else:
            for row in rows:
                printer(_format_draft_row(row))
        return True
    if cmd == "/view":
        if len(parts) < 2:
            printer("Usage: /view <draft_id> [style]")
            return True
        try:
            draft_id = int(parts[1])
        except ValueError:
            printer("draft_id must be an integer")
            return True
        style = parts[2] if len(parts) > 2 else None
        if style and style not in STYLE_VALUES:
            printer(f"style must be one of: {', '.join(STYLE_VALUES)}")
            return True
        draft = get_draft(conn, draft_id)
        if not draft:
            printer(f"Draft {draft_id} not found.")
            return True
        if style:
            rerender_draft(conn, draft_id, style)
            draft = get_draft(conn, draft_id)
        assert draft is not None
        printer(_format_draft_row(draft))
        printer("")
        printer(draft["content"])
        return True
    if cmd == "/post":
        if len(parts) < 2:
            printer("Usage: /post <draft_id|latest> [--dry-run]")
            return True
        dry_run = "--dry-run" in parts[2:] or "--dry-run" in parts[1:]
        if parts[1] == "latest":
            draft_id = _latest_publishable_draft_id(conn)
            if draft_id is None:
                printer("No pending/failed drafts to publish.")
                return True
        else:
            try:
                draft_id = int(parts[1])
            except ValueError:
                printer("draft_id must be integer or 'latest'")
                return True
        configured_cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post") or "opencli twitter post"
        rc = _publish_one(conn, draft_id, configured_cmd, dry_run=dry_run)
        if rc == 0:
            printer(f"Draft {draft_id} done ({'dry-run' if dry_run else 'published'}).")
        return True
    if cmd == "/retry":
        if len(parts) < 2:
            printer("Usage: /retry <draft_id|latest> [--dry-run]")
            return True
        dry_run = "--dry-run" in parts[2:] or "--dry-run" in parts[1:]
        if parts[1] == "latest":
            row = conn.execute(
                "SELECT id FROM drafts WHERE status = ? ORDER BY id DESC LIMIT 1",
                (STATUS_FAILED,),
            ).fetchone()
            draft_id = int(row["id"]) if row else None
            if draft_id is None:
                printer("No failed drafts to retry.")
                return True
        else:
            try:
                draft_id = int(parts[1])
            except ValueError:
                printer("draft_id must be integer or 'latest'")
                return True
        draft = get_draft(conn, draft_id)
        if not draft:
            printer(f"Draft {draft_id} not found.")
            return True
        if draft["status"] != STATUS_FAILED:
            printer(f"Draft {draft_id} is not FAILED (current={draft['status']}).")
            return True
        configured_cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post") or "opencli twitter post"
        rc = _publish_one(conn, draft_id, configured_cmd, dry_run=dry_run)
        if rc == 0:
            printer(f"Retry success for draft {draft_id} ({'dry-run' if dry_run else 'published'}).")
        return True
    if cmd == "/history":
        if len(parts) < 2:
            printer("Usage: /history <draft_id|latest> [limit]")
            return True
        if parts[1] == "latest":
            draft_id = _latest_draft_id(conn)
            if draft_id is None:
                printer("No drafts found.")
                return True
        else:
            try:
                draft_id = int(parts[1])
            except ValueError:
                printer("draft_id must be integer or 'latest'")
                return True
        limit = 10
        if len(parts) >= 3:
            try:
                limit = max(1, int(parts[2]))
            except ValueError:
                printer("limit must be integer")
                return True
        logs = list_publish_logs(conn, draft_id, limit=limit)
        if not logs:
            printer(f"No publish logs for draft {draft_id}.")
            return True
        for log in logs:
            kind = "dry-run" if int(log["dry_run"]) == 1 else "publish"
            printer(f"[{log['id']}] draft={draft_id} rc={log['return_code']} {kind} at {log['attempted_at']}")
            if log["stderr"]:
                printer(f"  stderr: {str(log['stderr']).strip()[:140]}")
            elif log["stdout"]:
                printer(f"  stdout: {str(log['stdout']).strip()[:140]}")
        return True
    if cmd == "/repos":
        if len(parts) < 3:
            printer("Usage: /repos <repo_path_a,repo_path_b> <merged|separate> [--dry-run]")
            return True
        repo_paths = [p.strip() for p in parts[1].split(",") if p.strip()]
        mode = parts[2]
        dry_run = "--dry-run" in parts[3:]
        if mode not in ("merged", "separate"):
            printer("mode must be merged or separate")
            return True
        repo_ids = find_repo_ids(conn, repo_paths)
        drafts = find_publishable_drafts(conn, repo_ids)
        if not drafts:
            printer("No pending/failed drafts found for selected repos.")
            return True
        configured_cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post") or "opencli twitter post"
        if mode == "separate":
            for draft in drafts:
                _publish_one(conn, int(draft["id"]), configured_cmd, dry_run=dry_run)
            return True
        merged_title, merged_content = _merge_draft_content(conn, [int(d["id"]) for d in drafts], "formal")
        payload = f"{merged_title}\n\n{merged_content}"
        result = publish_with_opencli(payload, base_command=configured_cmd, dry_run=dry_run)
        for draft in drafts:
            mark_publish_result(
                conn,
                draft_id=int(draft["id"]),
                command=result.command,
                return_code=result.return_code,
                stdout=result.stdout,
                stderr=result.stderr,
                dry_run=dry_run,
            )
        if result.return_code == 0:
            printer("Merged publish completed." if not dry_run else "Merged dry-run completed.")
        else:
            printer(f"Merged publish failed: {result.stderr or result.stdout}")
        return True
    if cmd == "/config":
        if len(parts) < 2:
            printer("Usage: /config show | /config set <key> <value>")
            return True
        if parts[1] == "show":
            rows = list_config(conn)
            if not rows:
                printer("No config found.")
            else:
                for row in rows:
                    printer(f"{row['key']}={row['value']}")
            return True
        if parts[1] == "set":
            if len(parts) < 4:
                printer("Usage: /config set <key> <value>")
                return True
            key = parts[2]
            value = " ".join(parts[3:])
            set_config(conn, key, value)
            printer(f"Config saved: {key}={value}")
            return True
        printer("Usage: /config show | /config set <key> <value>")
        return True
    printer("Unknown command. Use /help")
    return True


def _run_line_ui(conn) -> int:
    _print("CodeCast Interactive UI")
    _print("Welcome. Type a word command (no single-letter shortcuts). Type /exit to quit.")
    menu = [
        ("pending", "View pending drafts"),
        ("all", "View all drafts"),
        ("dry-run", "Dry-run latest draft"),
        ("publish", "Publish latest draft"),
        ("setup", "Quick setup (current repo)"),
        ("help", "Show slash command help"),
        ("refresh", "Refresh home"),
        ("exit", "Exit"),
    ]

    def print_menu() -> None:
        pending_count = count_drafts(conn, STATUS_PENDING)
        failed_count = count_drafts(conn, STATUS_FAILED)
        _print("")
        _print("==== Home ====")
        _print(f"Pending drafts: {pending_count} | Failed drafts: {failed_count}")
        if pending_count > 0:
            _print("Recommended: type 'pending' to review pending drafts.")
        elif failed_count > 0:
            _print("Recommended: type 'all' then '/retry latest'.")
        else:
            _print("Recommended: push code first, then return here.")
        for cmd, desc in menu:
            _print(f"- {cmd:8s} : {desc}")

    def print_next_hint() -> None:
        pending_count = count_drafts(conn, STATUS_PENDING)
        failed_count = count_drafts(conn, STATUS_FAILED)
        if pending_count > 0:
            _print("Next: type 'pending' to review drafts, then 'dry-run'.")
            return
        if failed_count > 0:
            _print("Next: type 'all' to list drafts, then '/retry latest'.")
            return
        _print("Next: create a commit and push, then type 'pending'.")

    print_menu()
    while True:
        try:
            raw = input("codecast> ").strip()
        except EOFError:
            _print("")
            return 0
        except KeyboardInterrupt:
            _print("")
            return 130
        if not raw:
            continue
        normalized = raw.strip().lower()
        if normalized in {"pending", "all", "dry-run", "publish", "setup", "help", "refresh", "exit"}:
            if normalized == "pending":
                rows = list_drafts(conn, status=STATUS_PENDING)
                if not rows:
                    _print("No pending drafts.")
                else:
                    for row in rows:
                        _print(_format_draft_row(row))
                print_next_hint()
                print_menu()
                continue
            if normalized == "all":
                rows = list_drafts(conn, status=None)
                if not rows:
                    _print("No drafts found.")
                else:
                    for row in rows:
                        _print(_format_draft_row(row))
                print_next_hint()
                print_menu()
                continue
            if normalized == "dry-run":
                draft_id = _latest_publishable_draft_id(conn)
                if draft_id is None:
                    _print("No pending/failed drafts to dry-run.")
                    print_next_hint()
                    print_menu()
                    continue
                configured_cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post") or "opencli twitter post"
                _publish_one(conn, draft_id, configured_cmd, dry_run=True)
                print_next_hint()
                print_menu()
                continue
            if normalized == "publish":
                draft_id = _latest_publishable_draft_id(conn)
                if draft_id is None:
                    _print("No pending/failed drafts to publish.")
                    print_next_hint()
                    print_menu()
                    continue
                confirm = input(f"Publish latest draft #{draft_id}? (y/N): ").strip().lower()
                if confirm != "y":
                    _print("Publish cancelled.")
                    print_next_hint()
                    print_menu()
                    continue
                configured_cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post") or "opencli twitter post"
                _publish_one(conn, draft_id, configured_cmd, dry_run=False)
                print_next_hint()
                print_menu()
                continue
            if normalized == "setup":
                ok, msg = _run_quick_setup(conn, None, None)
                _print(f"Quick setup {'completed' if ok else 'failed'}: {msg}")
                print_next_hint()
                print_menu()
                continue
            if normalized == "help":
                _ui_help()
                print_menu()
                continue
            if normalized == "refresh":
                print_menu()
                continue
            if normalized == "exit":
                return 0
        if not _handle_slash_command(conn, raw):
            return 0
        print_next_hint()
        print_menu()


def _style_next(style: str) -> str:
    idx = STYLE_VALUES.index(style) if style in STYLE_VALUES else 0
    return STYLE_VALUES[(idx + 1) % len(STYLE_VALUES)]


def _wrap_lines(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines() or [""]:
        wrapped = textwrap.wrap(line, width=max(10, width), replace_whitespace=False) or [""]
        lines.extend(wrapped)
    return lines


def _run_panel_ui(conn, db_path: str | None) -> int:
    def app(stdscr):
        def safe_add(win, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
            if width <= 0:
                return
            try:
                win.addnstr(y, x, text, width, attr)
            except curses.error:
                return

        curses.curs_set(0)
        onboarding_done = (get_config(conn, "ui.onboarding_done", "0") == "1")
        screen = "home" if onboarding_done else "onboarding"
        onboarding_step = 0
        onboarding_total = 3
        onboarding_setup_done = False
        selected = 0
        home_selected = 0
        show_all = False
        status_msg = "Ready"
        status_level = "info"
        home_items = [
            "View pending drafts",
            "View all drafts",
            "Dry-run latest draft",
            "Publish latest draft",
            "Set publish command",
            "Show slash command help",
            "Run quick setup (current repo)",
        ]
        action_log: list[str] = []

        def set_status(msg: str, level: str = "info") -> None:
            nonlocal status_msg, status_level
            status_msg = msg
            status_level = level
            action_log.insert(0, msg)
            if len(action_log) > 8:
                del action_log[8:]

        def open_slash_prompt() -> bool:
            h, w = stdscr.getmaxyx()
            curses.echo()
            curses.curs_set(1)
            stdscr.move(h - 1, 0)
            stdscr.clrtoeol()
            stdscr.addstr(h - 1, 0, "/")
            query = stdscr.getstr(h - 1, 1, w - 2).decode("utf-8", errors="ignore")
            curses.noecho()
            curses.curs_set(0)
            logs: list[str] = []
            keep = _handle_slash_command(conn, "/" + query, printer=logs.append)
            set_status(logs[-1] if logs else "Command executed", "ok")
            return keep

        def publish_latest(dry_run: bool) -> None:
            draft_id = _latest_publishable_draft_id(conn)
            if draft_id is None:
                set_status("No pending/failed draft found", "error")
                return
            cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post") or "opencli twitter post"
            rc = _publish_one(conn, draft_id, cmd, dry_run=dry_run)
            if rc == 0:
                set_status(
                    f"Latest draft #{draft_id} {'dry-run done' if dry_run else 'published'}",
                    "ok",
                )
            else:
                set_status(f"Latest draft #{draft_id} failed", "error")

        def draw_confirm(prompt: str) -> bool:
            h, w = stdscr.getmaxyx()
            width = min(max(44, len(prompt) + 8), max(44, w - 8))
            height = 7
            y = max(1, (h - height) // 2)
            x = max(2, (w - width) // 2)
            win = curses.newwin(height, width, y, x)
            win.keypad(True)
            while True:
                win.erase()
                win.border()
                win.addnstr(1, 2, "Confirm Publish", width - 4, curses.A_BOLD)
                for i, line in enumerate(_wrap_lines(prompt, width - 4)[:2]):
                    win.addnstr(2 + i, 2, line, width - 4)
                win.addnstr(height - 2, 2, "[y] Confirm   [n] Cancel", width - 4, curses.A_REVERSE)
                win.refresh()
                ch = win.getch()
                if ch in (ord("y"), ord("Y"), 10, 13):
                    return True
                if ch in (ord("n"), ord("N"), 27):
                    return False

        def draw_onboarding() -> None:
            h, w = stdscr.getmaxyx()
            stdscr.erase()
            box_w = min(max(68, w - 10), w - 4)
            box_h = min(max(14, h - 8), h - 4)
            y0 = max(1, (h - box_h) // 2)
            x0 = max(2, (w - box_w) // 2)
            win = curses.newwin(box_h, box_w, y0, x0)
            win.border()
            safe_add(win, 1, 2, "Welcome to CodeCast", box_w - 4, curses.A_BOLD)
            safe_add(win, 2, 2, f"Getting started ({onboarding_step + 1}/{onboarding_total})", box_w - 4)

            if onboarding_step == 0:
                lines = [
                    "CodeCast helps developers turn every git push",
                    "into a clear social update draft.",
                    "",
                    "You can review, dry-run, and publish from terminal UI.",
                ]
            elif onboarding_step == 1:
                lines = [
                    "Recommended workflow:",
                    "1) git push in your repo",
                    "2) open codecast",
                    "3) review pending drafts and publish",
                    "",
                    "Need command mode? Press '/' in any screen.",
                ]
            else:
                lines = [
                    "Quick setup can do this for you now:",
                    "- set publish command if missing",
                    "- install post-push hook for current repo",
                    "",
                    "Press [s] to run quick setup now.",
                    "Press [Enter] to continue without setup.",
                ]
                if onboarding_setup_done:
                    lines.append("")
                    lines.append("Quick setup has been executed.")

            row = 4
            for line in lines:
                if row >= box_h - 4:
                    break
                safe_add(win, row, 3, line, box_w - 6)
                row += 1

            if onboarding_step < onboarding_total - 1:
                footer = "[Enter] Next   [q] Quit"
            else:
                footer = "[s] Run setup   [Enter] Finish   [q] Quit"
            safe_add(win, box_h - 2, 2, footer, box_w - 4, curses.A_REVERSE)
            win.refresh()

        def draw_home() -> None:
            h, w = stdscr.getmaxyx()
            stdscr.erase()
            title = "CodeCast"
            subtitle = "Turn every git push into a polished social update."
            safe_add(stdscr, 1, 2, title, w - 4, curses.A_BOLD)
            safe_add(stdscr, 2, 2, subtitle, w - 4)
            safe_add(stdscr, 4, 2, "What it does:", w - 4, curses.A_BOLD)
            bullets = [
                "1) Collect changes from push/commit",
                "2) Build social draft automatically",
                "3) Let you dry-run / confirm publish",
            ]
            for i, line in enumerate(bullets):
                safe_add(stdscr, 5 + i, 4, line, w - 8)
            safe_add(stdscr, 9, 2, "Quick Actions:", w - 4, curses.A_BOLD)
            for i, item in enumerate(home_items):
                line = f"{i + 1}. {item}"
                y = 10 + i
                if y >= h - 10:
                    break
                if i == home_selected:
                    stdscr.attron(curses.A_REVERSE)
                    safe_add(stdscr, y, 4, line, w - 8)
                    stdscr.attroff(curses.A_REVERSE)
                else:
                    safe_add(stdscr, y, 4, line, w - 8)

            pending_count = count_drafts(conn, STATUS_PENDING)
            failed_count = count_drafts(conn, STATUS_FAILED)
            if pending_count > 0:
                next_step = f"Recommended: Press 1 to review {pending_count} pending draft(s)."
            elif failed_count > 0:
                next_step = f"Recommended: Go to drafts and retry {failed_count} failed draft(s) with key x."
            else:
                next_step = "Recommended: Make a commit and git push, then reopen drafts."
            safe_add(stdscr, h - 9, 2, next_step, w - 4, curses.A_BOLD)

            safe_add(stdscr, h - 8, 2, "Recent Activity:", w - 4, curses.A_BOLD)
            db_acts = list_recent_publish_activity(conn, limit=3)
            line_y = h - 7
            if db_acts:
                for act in db_acts[:3]:
                    dry = "dry-run" if int(act["dry_run"]) == 1 else "publish"
                    rc = int(act["return_code"])
                    repo = act["repo_name"] or "repo"
                    line = f"- #{act['id']} draft {act['draft_id']} {dry} rc={rc} [{repo}]"
                    safe_add(stdscr, line_y, 4, line, w - 8)
                    line_y += 1
                    if line_y >= h - 4:
                        break
            elif action_log:
                for msg in action_log[:3]:
                    safe_add(stdscr, line_y, 4, f"- {msg}", w - 8)
                    line_y += 1
                    if line_y >= h - 4:
                        break
            else:
                safe_add(stdscr, line_y, 4, "- No activity yet", w - 8)
            hint = "j/k or up/down move  Enter select  1-7 quick select  / command  q quit"
            safe_add(stdscr, h - 3, 0, hint, w - 1, curses.A_REVERSE)
            status_attr = curses.A_NORMAL
            if status_level == "ok":
                status_attr = curses.A_BOLD
            elif status_level == "error":
                status_attr = curses.A_REVERSE
            safe_add(stdscr, h - 2, 0, f"Home | {status_msg}", w - 1, status_attr)
            stdscr.refresh()

        def handle_home_action(index: int) -> bool:
            nonlocal screen, show_all, selected
            if index == 0:
                show_all = False
                selected = 0
                screen = "drafts"
                set_status("Opened pending drafts")
                return True
            if index == 1:
                show_all = True
                selected = 0
                screen = "drafts"
                set_status("Opened all drafts")
                return True
            if index == 2:
                publish_latest(dry_run=True)
                return True
            if index == 3:
                draft_id = _latest_publishable_draft_id(conn)
                if draft_id is None:
                    set_status("No pending/failed draft found", "error")
                    return True
                if not draw_confirm(f"Publish latest draft #{draft_id}?"):
                    set_status("Publish cancelled")
                    return True
                publish_latest(dry_run=False)
                return True
            if index == 4:
                current_cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post") or "opencli twitter post"
                h, w = stdscr.getmaxyx()
                curses.echo()
                curses.curs_set(1)
                stdscr.move(h - 1, 0)
                stdscr.clrtoeol()
                stdscr.addstr(h - 1, 0, f"publish.opencli_cmd [{current_cmd}] > ")
                value = stdscr.getstr().decode("utf-8", errors="ignore").strip()
                curses.noecho()
                curses.curs_set(0)
                if value:
                    set_config(conn, "publish.opencli_cmd", value)
                    set_status("Publish command updated", "ok")
                else:
                    set_status("Config unchanged")
                return True
            if index == 5:
                set_status("Try /help to list slash commands", "ok")
                return True
            if index == 6:
                ok, msg = _run_quick_setup(conn, db_path, None)
                set_status(msg, "ok" if ok else "error")
                return True
            return True

        def draw_drafts() -> None:
            nonlocal selected
            h, w = stdscr.getmaxyx()
            rows = list_drafts(conn, status=None if show_all else STATUS_PENDING)
            if rows and selected >= len(rows):
                selected = len(rows) - 1
            if selected < 0:
                selected = 0
            stdscr.erase()
            left_w = max(30, int(w * 0.44))
            split_x = left_w
            title = "CodeCast Draft Workspace"
            safe_add(stdscr, 0, 0, title, w - 1, curses.A_BOLD)
            for y in range(1, h - 3):
                try:
                    stdscr.addch(y, split_x, "|")
                except curses.error:
                    pass
            visible_rows = rows[: max(0, h - 4)]
            for i, row in enumerate(visible_rows):
                line = f"[{row['id']}] {row['status']} {row['repo_name'] or 'multi-repo'} {row['style']}"
                if i == selected:
                    stdscr.attron(curses.A_REVERSE)
                    safe_add(stdscr, i + 1, 0, line, left_w - 1)
                    stdscr.attroff(curses.A_REVERSE)
                else:
                    safe_add(stdscr, i + 1, 0, line, left_w - 1)
            if rows:
                draft = get_draft(conn, int(rows[selected]["id"]))
                if draft:
                    safe_add(stdscr, 1, split_x + 2, f"Draft {draft['id']} | {draft['status']} | {draft['style']}", w - split_x - 3)
                    for i, line in enumerate(_wrap_lines(draft["content"], w - split_x - 3)[: h - 6]):
                        safe_add(stdscr, i + 3, split_x + 2, line, w - split_x - 3)
            else:
                safe_add(stdscr, 2, split_x + 2, "No drafts available. Go back and run git push first.", w - split_x - 3)
            hints = "j/k move  p publish  d dry-run  x retry  h history  s style  a toggle  b home  / cmd  q quit"
            safe_add(stdscr, h - 3, 0, hints, w - 1, curses.A_REVERSE)
            mode_text = f"Drafts: {'ALL' if show_all else 'PENDING'}"
            status_line = f"{mode_text} | {status_msg}"
            status_attr = curses.A_NORMAL
            if status_level == "ok":
                status_attr = curses.A_BOLD
            elif status_level == "error":
                status_attr = curses.A_REVERSE
            safe_add(stdscr, h - 2, 0, status_line, w - 1, status_attr)
            stdscr.refresh()

        while True:
            if screen == "onboarding":
                draw_onboarding()
            elif screen == "home":
                draw_home()
            else:
                draw_drafts()
            rows = list_drafts(conn, status=None if show_all else STATUS_PENDING)
            ch = stdscr.getch()
            if ch in (ord("q"), 27):
                return
            if screen == "onboarding":
                if onboarding_step == onboarding_total - 1 and ch in (ord("s"), ord("S")):
                    ok, msg = _run_quick_setup(conn, db_path, None)
                    onboarding_setup_done = ok
                    set_status(msg, "ok" if ok else "error")
                    continue
                if ch in (10, 13):
                    if onboarding_step < onboarding_total - 1:
                        onboarding_step += 1
                    else:
                        set_config(conn, "ui.onboarding_done", "1")
                        screen = "home"
                        set_status("Onboarding completed", "ok")
                    continue
                continue
            if screen == "home":
                if ch in (ord("j"), curses.KEY_DOWN):
                    home_selected = min(home_selected + 1, len(home_items) - 1)
                    continue
                if ch in (ord("k"), curses.KEY_UP):
                    home_selected = max(0, home_selected - 1)
                    continue
                if ch in (10, 13):
                    if not handle_home_action(home_selected):
                        return
                    continue
                if ch in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5"), ord("6"), ord("7")):
                    idx = int(chr(ch)) - 1
                    if not handle_home_action(idx):
                        return
                    continue
                if ch == ord("/"):
                    if not open_slash_prompt():
                        return
                    continue
                continue
            if ch in (ord("j"), curses.KEY_DOWN):
                selected = min(selected + 1, max(0, len(rows) - 1))
                continue
            if ch in (ord("k"), curses.KEY_UP):
                selected = max(0, selected - 1)
                continue
            if ch == ord("b"):
                screen = "home"
                set_status("Back to home")
                continue
            if ch == ord("a"):
                show_all = not show_all
                set_status("Switched mode")
                continue
            if ch == ord("r"):
                set_status("Refreshed")
                continue
            if ch in (10, 13):
                if not rows:
                    set_status("No draft selected", "error")
                    continue
                draft_id = int(rows[selected]["id"])
                set_status(f"Viewing draft {draft_id}")
                continue
            if ch == ord("s"):
                if not rows:
                    set_status("No draft selected", "error")
                    continue
                draft_id = int(rows[selected]["id"])
                draft = get_draft(conn, draft_id)
                if not draft:
                    set_status("Draft missing", "error")
                    continue
                new_style = _style_next(str(draft["style"]))
                rerender_draft(conn, draft_id, new_style)
                set_status(f"Style -> {new_style}", "ok")
                continue
            if ch == ord("h"):
                if not rows:
                    set_status("No draft selected", "error")
                    continue
                draft_id = int(rows[selected]["id"])
                logs = list_publish_logs(conn, draft_id, limit=8)
                if not logs:
                    set_status(f"No history for draft {draft_id}")
                    continue
                h2, w2 = stdscr.getmaxyx()
                width = min(100, max(56, w2 - 6))
                height = min(14, max(8, h2 - 6))
                y0 = max(1, (h2 - height) // 2)
                x0 = max(2, (w2 - width) // 2)
                win = curses.newwin(height, width, y0, x0)
                win.keypad(True)
                while True:
                    win.erase()
                    win.border()
                    safe_add(win, 1, 2, f"Publish History (draft {draft_id})", width - 4, curses.A_BOLD)
                    row_y = 3
                    for lg in logs:
                        if row_y >= height - 2:
                            break
                        kind = "dry" if int(lg["dry_run"]) == 1 else "post"
                        line = f"#{lg['id']} rc={lg['return_code']} {kind} {lg['attempted_at']}"
                        safe_add(win, row_y, 2, line, width - 4)
                        row_y += 1
                    safe_add(win, height - 2, 2, "Press any key to close", width - 4, curses.A_REVERSE)
                    win.refresh()
                    _ = win.getch()
                    break
                set_status(f"Opened history for draft {draft_id}")
                continue
            if ch == ord("d"):
                if not rows:
                    set_status("No draft selected", "error")
                    continue
                draft_id = int(rows[selected]["id"])
                cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post") or "opencli twitter post"
                rc = _publish_one(conn, draft_id, cmd, dry_run=True)
                set_status("Dry-run success" if rc == 0 else "Dry-run failed", "ok" if rc == 0 else "error")
                continue
            if ch == ord("x"):
                if not rows:
                    set_status("No draft selected", "error")
                    continue
                draft_id = int(rows[selected]["id"])
                draft = get_draft(conn, draft_id)
                if not draft or draft["status"] != STATUS_FAILED:
                    set_status("Selected draft is not FAILED", "error")
                    continue
                if not draw_confirm(f"Retry failed draft #{draft_id}?"):
                    set_status("Retry cancelled")
                    continue
                cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post") or "opencli twitter post"
                rc = _publish_one(conn, draft_id, cmd, dry_run=False)
                set_status("Retry success" if rc == 0 else "Retry failed", "ok" if rc == 0 else "error")
                continue
            if ch == ord("p"):
                if not rows:
                    set_status("No draft selected", "error")
                    continue
                draft_id = int(rows[selected]["id"])
                if not draw_confirm(f"Publish draft #{draft_id} now?"):
                    set_status("Publish cancelled")
                    continue
                cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post") or "opencli twitter post"
                rc = _publish_one(conn, draft_id, cmd, dry_run=False)
                set_status("Published" if rc == 0 else "Publish failed", "ok" if rc == 0 else "error")
                continue
            if ch == ord("/"):
                if not open_slash_prompt():
                    return
                continue

    try:
        curses.wrapper(app)
    except curses.error:
        _print("Terminal panel rendering is not supported in this terminal; switched to plain mode.")
        return _run_line_ui(conn)
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    conn = connect(args.db_path)
    init_db(conn)
    plain = bool(getattr(args, "plain", False))
    term = os.getenv("TERM", "").lower()
    if plain or term in ("", "dumb", "unknown") or not sys.stdin.isatty() or not sys.stdout.isatty():
        return _run_line_ui(conn)
    return _run_panel_ui(conn, args.db_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codecast", description="CodeCast MVP CLI")
    parser.add_argument("--db-path", help="Override sqlite database path")
    parser.add_argument("--version", action="version", version=f"codecast {__version__}")
    sub = parser.add_subparsers(dest="command", required=False)
    parser.set_defaults(func=cmd_ui, plain=False)

    p_init = sub.add_parser("init", help="Initialize database")
    p_init.set_defaults(func=cmd_init)

    p_collect = sub.add_parser("collect", help="Collect push info from git range")
    p_collect.add_argument("--repo", help="Git repository path (default: current repo)")
    p_collect.add_argument("--oldrev", help="Old commit SHA")
    p_collect.add_argument("--newrev", help="New commit SHA")
    p_collect.set_defaults(func=cmd_collect)

    p_drafts = sub.add_parser("drafts", help="Manage drafts")
    drafts_sub = p_drafts.add_subparsers(dest="drafts_command", required=True)
    p_list = drafts_sub.add_parser("list", help="List drafts")
    p_list.add_argument("--all", action="store_true", help="List all statuses")
    p_list.set_defaults(func=cmd_drafts_list)
    p_render = drafts_sub.add_parser("render", help="Render drafts with style")
    p_render.add_argument("--draft", type=int, help="Draft id")
    p_render.add_argument("--style", choices=STYLE_VALUES, help="Style override")
    p_render.set_defaults(func=cmd_drafts_render)

    p_publish = sub.add_parser("publish", help="Publish drafts via opencli")
    p_publish.add_argument("--draft", type=int, help="Draft id")
    p_publish.add_argument("--repos", help="Comma-separated repo paths")
    p_publish.add_argument("--mode", choices=("merged", "separate"), default="separate")
    p_publish.add_argument("--style", choices=STYLE_VALUES, help="Style for merged content")
    p_publish.add_argument("--opencli-cmd", help="Base opencli command; default from config publish.opencli_cmd")
    p_publish.add_argument("--dry-run", action="store_true", help="Do not execute command")
    p_publish.set_defaults(func=cmd_publish)

    p_settings = sub.add_parser("settings", help="Per-repo settings")
    settings_sub = p_settings.add_subparsers(dest="settings_command", required=True)
    p_set = settings_sub.add_parser("set", help="Set repository settings")
    p_set.add_argument("--repo", help="Repository path (default: current repo)")
    p_set.add_argument("--every-n-pushes", type=int, help="Aggregate threshold per draft")
    p_set.add_argument("--publish-enabled", type=lambda x: x.lower() in ("1", "true", "yes", "on"))
    p_set.add_argument("--default-style", choices=STYLE_VALUES)
    p_set.set_defaults(func=cmd_settings_set)

    p_hook = sub.add_parser("install-hook", help="Install post-push hook for a repo")
    p_hook.add_argument("--repo", help="Git repository path (default: current repo)")
    p_hook.set_defaults(func=cmd_install_hook)

    p_setup = sub.add_parser("setup", help="Quick setup: config publish command and install hook")
    p_setup.add_argument("--repo", help="Git repository path (default: detect current repo)")
    p_setup.set_defaults(func=cmd_setup)

    p_onboarding = sub.add_parser("onboarding", help="Manage onboarding state")
    onboarding_sub = p_onboarding.add_subparsers(dest="onboarding_command", required=True)
    p_onboarding_status = onboarding_sub.add_parser("status", help="Show onboarding state")
    p_onboarding_status.set_defaults(func=cmd_onboarding_status)
    p_onboarding_reset = onboarding_sub.add_parser("reset", help="Reset onboarding to show again")
    p_onboarding_reset.set_defaults(func=cmd_onboarding_reset)
    p_onboarding_complete = onboarding_sub.add_parser("complete", help="Mark onboarding as completed")
    p_onboarding_complete.set_defaults(func=cmd_onboarding_complete)

    p_config = sub.add_parser("config", help="Global config")
    config_sub = p_config.add_subparsers(dest="config_command", required=True)
    p_config_set = config_sub.add_parser("set", help="Set config key/value")
    p_config_set.add_argument("--key", required=True, help="Config key")
    p_config_set.add_argument("--value", required=True, help="Config value")
    p_config_set.set_defaults(func=cmd_config_set)
    p_config_get = config_sub.add_parser("get", help="Get config value(s)")
    p_config_get.add_argument("--key", help="Config key (omit to list all)")
    p_config_get.set_defaults(func=cmd_config_get)

    p_ui = sub.add_parser("ui", help="Open interactive terminal UI")
    p_ui.add_argument("--plain", action="store_true", help="Force slash-command plain mode")
    p_ui.set_defaults(func=cmd_ui)

    p_cast = sub.add_parser("cast", help="Open panel mode UI (Cloud-Code style)")
    p_cast.add_argument("--plain", action="store_true", help="Force slash-command plain mode")
    p_cast.set_defaults(func=cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except GitError as exc:
        _print(f"Error: {exc}")
        return 1
    except KeyboardInterrupt:
        _print("Interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
