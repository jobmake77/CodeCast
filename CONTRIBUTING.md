# Contributing to CodeCast

Thanks for your interest in contributing.

## Getting started

1. Fork the repo and create a branch from `main`.
2. Install and run tests locally.

```bash
python3 -m pip install --user /path/to/CodeCast
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Development guidelines

- Keep CLI behavior backward-compatible when possible.
- Prefer small, focused PRs.
- Update docs when changing UX, commands, or key bindings.
- Add tests for behavior changes.

## Commit style (recommended)

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation change
- `refactor:` internal refactor
- `test:` tests only
- `chore:` maintenance

## Pull request checklist

- [ ] Tests pass locally.
- [ ] README/docs updated if behavior changed.
- [ ] New behavior is covered by tests.
- [ ] No unrelated file changes.

## Reporting issues

Please use GitHub issue templates and include:

- OS and Python version
- command you ran
- expected vs actual behavior
- relevant logs or screenshots

