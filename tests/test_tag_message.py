"""scripts/check-tag-message.sh: a release tag's message, its notes on GitHub,
is an annotated tag's and mentions no one (Release binaries in
docs/spec/deployment.md)."""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check-tag-message.sh"


def check(message):
    return subprocess.run(["sh", str(SCRIPT), "-"], input=message, capture_output=True, text=True)


@pytest.mark.parametrize("message", [
    "- **@name** completes a user's name in the editor",
    "@anna fixed it",
    "thanks (@anna)",
    "teams like @zonewiki/core",
])
def test_a_bare_mention_is_refused(message):
    result = check("Zonewiki 0.7.0\n\n" + message + "\n")
    assert result.returncode == 1
    assert "line 3: " + message in result.stderr
    assert "`@name`" in result.stderr


@pytest.mark.parametrize("message", [
    "- **`@name`** completes a user's name in the editor",
    "write to a@example.org",
    "```\n@decorator\ndef f(): pass\n```",
    "~~~\n@anna\n~~~",
    "an @ alone, or @-prefixed",
    "",
])
def test_code_addresses_and_plain_text_pass(message):
    result = check("Zonewiki 0.7.0\n\n" + message + "\n")
    assert result.returncode == 0, result.stderr


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
                   cwd=cwd, check=True, capture_output=True)


def test_a_tag_must_be_annotated(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "c")
    git(tmp_path, "tag", "v1.0.0")
    git(tmp_path, "tag", "-a", "v1.0.1", "-m", "Zonewiki 1.0.1\n\n- **@name** chips")
    git(tmp_path, "tag", "-a", "v1.0.2", "-m", "Zonewiki 1.0.2\n\n- **`@name`** chips")

    def run(tag):
        return subprocess.run(["sh", str(SCRIPT), tag], cwd=tmp_path, capture_output=True, text=True)

    lightweight = run("v1.0.0")
    assert lightweight.returncode == 1 and "isn't an annotated tag" in lightweight.stderr
    mentioning = run("v1.0.1")
    assert mentioning.returncode == 1 and "line 3: - **@name** chips" in mentioning.stderr
    assert run("v1.0.2").returncode == 0
