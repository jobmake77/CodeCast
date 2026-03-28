from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from subprocess import run
import subprocess
import os

from codecast.cli import main
from codecast.storage import STATUS_ARCHIVED, STATUS_FAILED, STATUS_PENDING, connect, get_draft, init_db


def git(repo: Path, *args: str) -> str:
    proc = run(["git", *args], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()


class CodeCastCLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "codecast.db"
        self.repo = self.root / "repo-a"
        self.repo.mkdir(parents=True)
        git(self.repo, "init")
        git(self.repo, "config", "user.email", "dev@example.com")
        git(self.repo, "config", "user.name", "Dev")
        (self.repo / "a.txt").write_text("one\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-m", "feat: init")
        (self.repo / "a.txt").write_text("one\ntwo\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-m", "fix: patch")
        (self.repo / "b.txt").write_text("hello\n")
        git(self.repo, "add", "b.txt")
        git(self.repo, "commit", "-m", "refactor: extract flow")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_collect_with_every_n_pushes(self) -> None:
        self.assertEqual(main(["--db-path", str(self.db_path), "init"]), 0)
        self.assertEqual(
            main(
                [
                    "--db-path",
                    str(self.db_path),
                    "settings",
                    "set",
                    "--repo",
                    str(self.repo),
                    "--every-n-pushes",
                    "2",
                ]
            ),
            0,
        )
        c1 = git(self.repo, "rev-parse", "HEAD~2")
        c2 = git(self.repo, "rev-parse", "HEAD~1")
        c3 = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(
            main(
                [
                    "--db-path",
                    str(self.db_path),
                    "collect",
                    "--repo",
                    str(self.repo),
                    "--oldrev",
                    c1,
                    "--newrev",
                    c2,
                ]
            ),
            0,
        )
        self.assertEqual(
            main(
                [
                    "--db-path",
                    str(self.db_path),
                    "collect",
                    "--repo",
                    str(self.repo),
                    "--oldrev",
                    c2,
                    "--newrev",
                    c3,
                ]
            ),
            0,
        )
        conn = connect(str(self.db_path))
        init_db(conn)
        pending = conn.execute("SELECT COUNT(*) AS c FROM drafts WHERE status = ?", (STATUS_PENDING,)).fetchone()
        self.assertEqual(int(pending["c"]), 1)

    def test_publish_archives_draft(self) -> None:
        self.assertEqual(main(["--db-path", str(self.db_path), "init"]), 0)
        c2 = git(self.repo, "rev-parse", "HEAD~1")
        c3 = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(
            main(
                [
                    "--db-path",
                    str(self.db_path),
                    "collect",
                    "--repo",
                    str(self.repo),
                    "--oldrev",
                    c2,
                    "--newrev",
                    c3,
                ]
            ),
            0,
        )
        conn = connect(str(self.db_path))
        init_db(conn)
        row = conn.execute("SELECT id FROM drafts ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        draft_id = int(row["id"])
        self.assertEqual(
            main(
                [
                    "--db-path",
                    str(self.db_path),
                    "publish",
                    "--draft",
                    str(draft_id),
                    "--opencli-cmd",
                    "/usr/bin/true",
                ]
            ),
            0,
        )
        draft = get_draft(conn, draft_id)
        self.assertIsNotNone(draft)
        assert draft is not None
        self.assertEqual(draft["status"], STATUS_ARCHIVED)

    def test_collect_supports_first_push_zero_oldrev(self) -> None:
        only_repo = self.root / "repo-zero"
        only_repo.mkdir(parents=True)
        git(only_repo, "init")
        git(only_repo, "config", "user.email", "dev@example.com")
        git(only_repo, "config", "user.name", "Dev")
        (only_repo / "readme.md").write_text("init\n")
        git(only_repo, "add", "readme.md")
        git(only_repo, "commit", "-m", "feat: first commit")
        head = git(only_repo, "rev-parse", "HEAD")

        self.assertEqual(main(["--db-path", str(self.db_path), "init"]), 0)
        self.assertEqual(
            main(
                [
                    "--db-path",
                    str(self.db_path),
                    "collect",
                    "--repo",
                    str(only_repo),
                    "--oldrev",
                    "0000000000000000000000000000000000000000",
                    "--newrev",
                    head,
                ]
            ),
            0,
        )
        conn = connect(str(self.db_path))
        init_db(conn)
        pending = conn.execute("SELECT COUNT(*) AS c FROM drafts WHERE status = ?", (STATUS_PENDING,)).fetchone()
        self.assertEqual(int(pending["c"]), 1)

    def test_ui_starts_and_exits(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path("/Users/a77/Desktop/CodeCast/src"))
        proc = subprocess.run(
            [
                "python3",
                "-m",
                "codecast.cli",
                "--db-path",
                str(self.db_path),
                "ui",
            ],
            input="/exit\n",
            text=True,
            capture_output=True,
            env=env,
            cwd="/Users/a77/Desktop/CodeCast",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("CodeCast client", proc.stdout)
        self.assertIn("next:", proc.stdout)
        self.assertNotIn("All commands:", proc.stdout)

    def test_default_command_enters_ui(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path("/Users/a77/Desktop/CodeCast/src"))
        proc = subprocess.run(
            [
                "python3",
                "-m",
                "codecast.cli",
                "--db-path",
                str(self.db_path),
            ],
            input="/exit\n",
            text=True,
            capture_output=True,
            env=env,
            cwd="/Users/a77/Desktop/CodeCast",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("CodeCast client", proc.stdout)

    def test_ui_do_reviews_when_pending_exists(self) -> None:
        self.assertEqual(main(["--db-path", str(self.db_path), "init"]), 0)
        c2 = git(self.repo, "rev-parse", "HEAD~1")
        c3 = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(
            main(
                [
                    "--db-path",
                    str(self.db_path),
                    "collect",
                    "--repo",
                    str(self.repo),
                    "--oldrev",
                    c2,
                    "--newrev",
                    c3,
                ]
            ),
            0,
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path("/Users/a77/Desktop/CodeCast/src"))
        proc = subprocess.run(
            [
                "python3",
                "-m",
                "codecast.cli",
                "--db-path",
                str(self.db_path),
                "ui",
            ],
            input="do\nexit\n",
            text=True,
            capture_output=True,
            env=env,
            cwd="/Users/a77/Desktop/CodeCast",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Opened draft #", proc.stdout)

    def test_ui_do_retries_when_only_failed_exists(self) -> None:
        self.assertEqual(main(["--db-path", str(self.db_path), "init"]), 0)
        c2 = git(self.repo, "rev-parse", "HEAD~1")
        c3 = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(
            main(
                [
                    "--db-path",
                    str(self.db_path),
                    "collect",
                    "--repo",
                    str(self.repo),
                    "--oldrev",
                    c2,
                    "--newrev",
                    c3,
                ]
            ),
            0,
        )
        conn = connect(str(self.db_path))
        init_db(conn)
        row = conn.execute("SELECT id FROM drafts ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None
        draft_id = int(row["id"])
        conn.execute("UPDATE drafts SET status = ? WHERE id = ?", (STATUS_FAILED, draft_id))
        conn.commit()
        self.assertEqual(
            main(
                [
                    "--db-path",
                    str(self.db_path),
                    "config",
                    "set",
                    "--key",
                    "publish.opencli_cmd",
                    "--value",
                    "/usr/bin/false",
                ]
            ),
            0,
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path("/Users/a77/Desktop/CodeCast/src"))
        proc = subprocess.run(
            [
                "python3",
                "-m",
                "codecast.cli",
                "--db-path",
                str(self.db_path),
                "ui",
            ],
            input="do\nexit\n",
            text=True,
            capture_output=True,
            env=env,
            cwd="/Users/a77/Desktop/CodeCast",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Failed publishing draft", proc.stdout)

    def test_ui_navigation_more_help_full_back(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path("/Users/a77/Desktop/CodeCast/src"))
        proc = subprocess.run(
            [
                "python3",
                "-m",
                "codecast.cli",
                "--db-path",
                str(self.db_path),
                "ui",
            ],
            input="more\nhelp full\nback\nexit\n",
            text=True,
            capture_output=True,
            env=env,
            cwd="/Users/a77/Desktop/CodeCast",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("More commands:", proc.stdout)
        self.assertIn("All commands:", proc.stdout)
        self.assertIn("main: do", proc.stdout)

    def test_restart_command_enters_ui(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path("/Users/a77/Desktop/CodeCast/src"))
        proc = subprocess.run(
            [
                "python3",
                "-m",
                "codecast.cli",
                "--db-path",
                str(self.db_path),
                "restart",
            ],
            input="exit\n",
            text=True,
            capture_output=True,
            env=env,
            cwd="/Users/a77/Desktop/CodeCast",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Restarting CodeCast client...", proc.stdout)
        self.assertIn("CodeCast client", proc.stdout)


if __name__ == "__main__":
    unittest.main()
