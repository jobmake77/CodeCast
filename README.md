# CodeCast

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](#requirements)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](#license)
[![UI](https://img.shields.io/badge/interface-terminal%20panel-black)](#panel-ui)
[![Status](https://img.shields.io/badge/status-mvp-orange)](#roadmap)

Turn every `git push` into a polished social update.

CodeCast listens to your push workflow, creates drafts from commit activity, and publishes through `opencli` after manual confirmation.

中文文档: [README.zh-CN.md](./README.zh-CN.md)

## Why CodeCast

- No more "I shipped a lot but posted nothing."
- Push-level aggregation (not noisy commit-by-commit spam).
- Terminal-first panel UI (`codecast`) with keyboard shortcuts.
- Manual confirmation before publish.
- Per-repo settings.
- Publish via `opencli` (Twitter/X and other adapters).

## Demo Flow (30 seconds)

1. Write code and `git push`.
2. Open `codecast` panel.
3. Review draft, switch style, dry-run.
4. Confirm and publish.

## Architecture

```mermaid
flowchart LR
    A["git push"] --> B["post-push hook"]
    B --> C["codecast collect"]
    C --> D["SQLite (events/commits/drafts/logs)"]
    D --> E["codecast panel UI"]
    E --> F["opencli twitter post"]
    F --> G["Twitter/X"]
```

## UI Preview

Terminal panel (default entry: `codecast`):

```text
CodeCast Panel
[12] PENDING CodeCast formal
[11] FAILED  repo-a  friendly
-----------------------------------------------
Draft 12 | PENDING | formal
Progress update for CodeCast...

j/k move  p publish  d dry-run  x retry  h history  / command  q quit
```

## Features

- Push collection into local SQLite.
- Draft lifecycle: `PENDING -> FAILED/PUBLISHED -> ARCHIVED`.
- Style presets: `formal`, `friendly`, `punchy`.
- Multi-repo publish: `merged` or `separate`.
- Publish history popup and failed retry.
- Slash commands and panel mode in one tool.

## Installation

### Option A: Install for current user (recommended)

```bash
python3 -m pip install --user /path/to/CodeCast
```

If `codecast` is not found, add this to your shell profile:

```bash
export PATH="$HOME/Library/Python/3.9/bin:$PATH"
```

### Option B: Run from source directly

```bash
cd /path/to/CodeCast
PYTHONPATH=src python3 -m codecast.cli
```

## Quick Start

Run once:

```bash
codecast init
codecast config set --key publish.opencli_cmd --value "opencli twitter post"
codecast install-hook --repo /path/to/your/repo
```

Then in your dev repo:

```bash
git add .
git commit -m "feat: ship something"
git push
```

Open panel:

```bash
codecast
```

## Panel UI

`codecast` opens panel mode by default.

### Key bindings

```text
j / k / ↑ / ↓  Move selection
p              Publish selected draft (with confirm popup)
d              Dry-run selected draft
x              Retry selected FAILED draft
h              Open publish history popup
s              Cycle style (formal/friendly/punchy)
a              Toggle list mode (pending/all)
/              Open slash command
q              Quit
```

## Slash Commands

```text
/pending
/all
/view <draft_id> [style]
/post <draft_id|latest> [--dry-run]
/retry <draft_id|latest> [--dry-run]
/history <draft_id|latest> [limit]
/repos <repo_a,repo_b> <merged|separate> [--dry-run]
/config show
/config set <key> <value>
/exit
```

## CLI Commands

```bash
codecast init
codecast collect --repo /path/to/repo --oldrev <old_sha> --newrev <new_sha>
codecast drafts list --all
codecast drafts render --draft 1 --style friendly
codecast publish --draft 1 --dry-run
codecast publish --repos /repo/a,/repo/b --mode merged
codecast settings set --repo /repo/a --every-n-pushes 10 --default-style friendly
codecast install-hook --repo /repo/a
codecast ui --plain
```

## Configuration

- `publish.opencli_cmd`: publish command (example: `opencli twitter post`)
- `publish.every_n_pushes`: per-repo aggregation threshold (`settings set --every-n-pushes`)
- `publish_enabled`: per-repo publish switch (`settings set --publish-enabled true|false`)
- `style.default`: per-repo default style (`settings set --default-style`)

## Requirements

- Python 3.9+
- Git
- `opencli` for real publishing
- Chrome + opencli Browser Bridge extension (for browser-backed adapters like Twitter/X)

## FAQ

### Why publish failed with "Extension is not connected"?

Your `opencli` daemon is running but Chrome extension is not connected.
Install/load the extension and run `opencli doctor` until it reports connected.

### Where is data stored?

Default database:

```text
~/.codecast/codecast.db
```

Override with:

```bash
CODECAST_DB_PATH=/custom/path/codecast.db
```

### Is auto-publish enabled?

No. MVP keeps manual confirmation before real publish.

## Roadmap

- Richer draft templates and prompt packs.
- Better onboarding wizard (`codecast setup`).
- Optional web UI on top of the same local DB.
- Plugin-style publisher backends.

## Contributing

Issues and PRs are welcome.  
If you propose UX changes, please include:

- before/after behavior
- keyboard flow impact
- command compatibility notes

For major changes, open an issue first so we can align on behavior and CLI compatibility.

See:

- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)

## License

[MIT](./LICENSE)
