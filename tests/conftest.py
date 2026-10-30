import os
import subprocess
from pathlib import Path

import pytest


class FakeRepo:
    """A throwaway Git repo where every commit has a scripted author and date."""

    def __init__(self, path: Path):
        self.path = path
        self.t = 1_700_000_000
        self._git("init", "-q", "-b", "main")

    def _git(self, *args, env=None):
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
            cwd=self.path, check=True, capture_output=True, env=env,
        )

    def commit(self, author: str, files: dict[str, str | None] | None = None,
               msg: str = "change", email: str | None = None, rename: tuple[str, str] | None = None,
               days: float = 1):
        """Write files (None deletes), optionally `git mv`, then commit as `author`."""
        for name, content in (files or {}).items():
            p = self.path / name
            if content is None:
                self._git("rm", "-q", name)
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            self._git("add", name)
        if rename:
            (self.path / rename[1]).parent.mkdir(parents=True, exist_ok=True)
            self._git("mv", *rename)
        self.t += int(days * 86400)
        email = email or f"{author.split()[0].lower()}@example.com"
        date = f"{self.t} +0000"
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": date,
        }
        self._git("commit", "-q", "--allow-empty", "-m", msg, env=env)


@pytest.fixture
def repo(tmp_path) -> FakeRepo:
    return FakeRepo(tmp_path)


def body(n: int) -> str:
    """Distinct file content per revision, long enough for rename detection to match."""
    lines = [f"def f{i}():\n    return {i}\n" for i in range(20)]
    return "".join(lines) + f"# rev {n}\n"
