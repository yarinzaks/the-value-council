"""The daily run's commit-and-push step, executed against real git.

The step is shell inside YAML, so nothing type-checks it and a mistake
in it shows up only as a run whose data never reached the repo. Two
did (audit RK-09):

* ``data/memos`` was written on every run and never staged, so the
  analyst layer's output stayed on the runner and was thrown away.
* ``git pull --rebase ... || true`` swallowed a failed rebase. A
  conflicted rebase leaves HEAD detached on the remote tip, so the
  ``git push origin HEAD:<ref>`` after it pushed nothing new and the job
  went green while the run's commit was discarded with the runner.

These tests lift the step's ``run:`` block out of the workflow text and
run it the way Actions does — ``bash -e`` — inside a throwaway clone of
a throwaway bare remote. No network and no global git config: the
clone sees only what the test gives it.
"""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

DAILY = (
    Path(__file__).resolve().parents[2]
    / ".github" / "workflows" / "daily-paper-trading.yml"
)
STEP_NAME = "Commit updated state back to the repo"
BRANCH = "main"


def _step_script(text: str, name: str) -> str:
    """The dedented ``run: |`` body of the step called ``name``."""
    lines = text.splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.strip() == f"- name: {name}"
    )
    run_at = next(
        i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |"
    )
    indent = len(lines[run_at]) - len(lines[run_at].lstrip())
    body: list[str] = []
    for line in lines[run_at + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    return textwrap.dedent("\n".join(body)) + "\n"


SCRIPT = _step_script(DAILY.read_text(), STEP_NAME)


def _staged_paths(script: str) -> list[str]:
    """The pathspecs the step's ``git add`` names, continuation lines included."""
    joined = script.replace("\\\n", " ")
    match = re.search(r"^\s*git add (?P<paths>[^\n]+)$", joined, re.M)
    assert match is not None, "the commit step no longer runs `git add`"
    return match["paths"].split()


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        HOME=str(home),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )
    return env


def _git(cwd: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def _seed(tree: Path) -> None:
    """Give every pathspec the step stages something to match.

    In Actions the checkout and the ``mkdir -p`` step provide them; a
    pathspec that matches nothing makes ``git add`` exit 128.
    """
    for spec in _staged_paths(SCRIPT):
        target = tree / spec
        if target.suffix:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}\n")
        else:
            target.mkdir(parents=True, exist_ok=True)
            (target / ".gitkeep").write_text("")


class Repo:
    """A bare remote, a clone the step runs in, and a second writer."""

    def __init__(self, root: Path) -> None:
        self.env = _env(root / "home")
        (root / "home").mkdir()
        self.remote = root / "remote.git"
        self.work = root / "runner"
        self.other = root / "other"
        _git(root, self.env, "init", "--bare", "-b", BRANCH, str(self.remote))
        _git(root, self.env, "clone", str(self.remote), str(self.work))
        _seed(self.work)
        (self.work / "data" / "portfolios" / "graham.json").write_text('{"cash": 1}\n')
        _git(self.work, self.env, "add", ".")
        _git(self.work, self.env, "commit", "-m", "seed")
        _git(self.work, self.env, "push", "origin", f"HEAD:{BRANCH}")
        _git(root, self.env, "clone", str(self.remote), str(self.other))

    def push_from_other(self, rel: str, content: str) -> str:
        path = self.other / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        _git(self.other, self.env, "add", rel)
        _git(self.other, self.env, "commit", "-m", f"other writes {rel}")
        _git(self.other, self.env, "push", "origin", f"HEAD:{BRANCH}")
        return _git(self.other, self.env, "rev-parse", "HEAD")

    def run_step(self) -> subprocess.CompletedProcess[str]:
        env = dict(self.env, MODE="open", MARKET="US", TARGET_REF=BRANCH)
        # `bash -e {0}` is what Actions runs a step with no `shell:` under.
        return subprocess.run(
            ["bash", "-e", "-c", SCRIPT], cwd=self.work, env=env,
            capture_output=True, text=True,
        )

    def remote_head(self) -> str:
        return _git(self.remote, self.env, "rev-parse", BRANCH)

    def remote_file(self, rel: str) -> str | None:
        result = subprocess.run(
            ["git", "show", f"{BRANCH}:{rel}"], cwd=self.remote, env=self.env,
            capture_output=True, text=True,
        )
        return result.stdout if result.returncode == 0 else None

    def rebase_in_progress(self) -> bool:
        for name in ("rebase-merge", "rebase-apply"):
            rel = _git(self.work, self.env, "rev-parse", "--git-path", name)
            if (self.work / rel).exists():
                return True
        return False


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def test_the_step_is_found() -> None:
    """A renamed step would otherwise empty every test below."""
    assert "git push" in SCRIPT
    assert "git add" in SCRIPT


def test_memos_are_staged(repo: Repo) -> None:
    """The analyst layer's memos reach the repo (audit RK-09)."""
    assert "data/memos" in _staged_paths(SCRIPT)
    memo = "data/memos/benjamin_graham/2026-11-13.json"
    (repo.work / memo).parent.mkdir(parents=True, exist_ok=True)
    (repo.work / memo).write_text('{"verdict": "WATCH"}\n')

    result = repo.run_step()

    assert result.returncode == 0, result.stderr
    assert repo.remote_file(memo) == '{"verdict": "WATCH"}\n'


def test_nothing_changed_pushes_nothing(repo: Repo) -> None:
    before = repo.remote_head()
    result = repo.run_step()
    assert result.returncode == 0, result.stderr
    assert "nothing to commit" in result.stdout
    assert repo.remote_head() == before


def test_an_unrelated_remote_commit_is_rebased_onto(repo: Repo) -> None:
    """The ordinary race: someone pushed elsewhere while the scan ran."""
    (repo.work / "data/portfolios/graham.json").write_text('{"cash": 2}\n')
    theirs = repo.push_from_other("README.md", "hello\n")

    result = repo.run_step()

    assert result.returncode == 0, result.stderr
    assert repo.remote_file("data/portfolios/graham.json") == '{"cash": 2}\n'
    assert repo.remote_file("README.md") == "hello\n"
    log = _git(repo.remote, repo.env, "rev-list", BRANCH)
    assert theirs in log.split()


def test_a_conflicting_remote_commit_fails_the_job(repo: Repo) -> None:
    """A failed rebase is a failed run, not a green one that pushed nothing.

    Before the fix this exited 0: the rebase stopped on the conflict,
    ``|| true`` hid it, and ``git push origin HEAD:main`` from the
    detached remote tip reported "Everything up-to-date".
    """
    (repo.work / "data/portfolios/graham.json").write_text('{"cash": 2}\n')
    (repo.work / "data/sectors.json").write_text('{"X": "ours"}\n')
    repo.push_from_other("data/portfolios/graham.json", '{"cash": 3}\n')
    theirs = repo.push_from_other("data/sectors.json", '{"X": "theirs"}\n')

    result = repo.run_step()

    assert result.returncode != 0
    errors = [ln for ln in result.stdout.splitlines() if ln.startswith("::error::")]
    assert len(errors) == 1, result.stdout
    # Both files on the annotation's one line: GitHub drops what follows a newline.
    assert "data/portfolios/graham.json" in errors[0]
    assert "data/sectors.json" in errors[0]
    # The remote keeps what the other writer pushed, untouched.
    assert repo.remote_head() == theirs
    assert repo.remote_file("data/portfolios/graham.json") == '{"cash": 3}\n'
    # And the runner is not left mid-rebase for a later step to trip on.
    assert not repo.rebase_in_progress()


def test_an_unreachable_remote_fails_the_job(repo: Repo) -> None:
    """A pull that fails before any rebase starts is a failure too."""
    (repo.work / "data/portfolios/graham.json").write_text('{"cash": 2}\n')
    _git(repo.work, repo.env, "remote", "set-url", "origin",
         str(repo.remote.parent / "gone.git"))

    result = repo.run_step()

    assert result.returncode != 0
    assert "the pull itself failed" in result.stdout
    assert not repo.rebase_in_progress()
