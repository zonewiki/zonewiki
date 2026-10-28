"""run.sh's container calls and the pre-commit hook, against a fake engine.

A stub `podman` first on PATH records its arguments, so these run without a
container engine. run.sh and the hook are copied into a scratch git repo:
run.sh works in its own folder, and git calls with a temporary index must not
touch this repo's.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

FAKE_ENGINE = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1" in
  build)
    echo "debconf: noise a RUN step prints"
    [ -z "$FAKE_BUILD_FAILS" ] || exit 1
    while [ "$1" != --iidfile ]; do shift; done
    echo fake-image > "$2" ;;
  create) echo fake-container ;;
  image) exit 1 ;;
  run|start) exit "${FAKE_EXIT:-0}" ;;
esac
exit 0
"""

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")


def _git(cwd, *args, env=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=cwd, env=env, capture_output=True, text=True)


@pytest.fixture
def sandbox(tmp_path):
    """A git repo with run.sh and the hook, and an env whose engine is the stub."""
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    shutil.copy(REPO / "run.sh", repo / "run.sh")
    shutil.copy(REPO / ".githooks" / "pre-commit", repo / ".githooks" / "pre-commit")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    engine = bin_dir / "podman"
    engine.write_text(FAKE_ENGINE)
    engine.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "LEAF_"))}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_LOG"] = str(tmp_path / "engine.log")
    _git(repo, "init", "-q")
    _git(repo, "config", "core.hooksPath", ".githooks")
    (repo / "notes.txt").write_text("one\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "--no-verify", "-m", "init")
    return repo, env, tmp_path / "engine.log"


def _engine_calls(log, verb):
    return [l for l in log.read_text().splitlines() if l.startswith(verb + " ")]


def test_leaks_passes_an_absolute_index_inside_the_repo(sandbox):
    repo, env, log = sandbox
    env["GIT_INDEX_FILE"] = str(repo / ".git" / "index.lock")
    r = subprocess.run(["./run.sh", "leaks", "--pre-commit", "--staged"],
                       cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (run,) = _engine_calls(log, "run")
    assert "-e GIT_INDEX_FILE=/repo/.git/index.lock" in run


def test_leaks_passes_a_relative_index(sandbox):
    repo, env, log = sandbox
    env["GIT_INDEX_FILE"] = ".git/next-index-1.lock"
    r = subprocess.run(["./run.sh", "leaks"], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (run,) = _engine_calls(log, "run")
    assert "-e GIT_INDEX_FILE=/repo/.git/next-index-1.lock" in run


def test_leaks_refuses_an_index_outside_the_repo(sandbox, tmp_path):
    repo, env, log = sandbox
    env["GIT_INDEX_FILE"] = str(tmp_path / "elsewhere.lock")
    r = subprocess.run(["./run.sh", "leaks"], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 2
    assert "outside the repo" in r.stderr
    assert not log.exists() or not _engine_calls(log, "run")


def test_leaks_without_an_index_passes_none(sandbox):
    repo, env, log = sandbox
    r = subprocess.run(["./run.sh", "leaks"], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (run,) = _engine_calls(log, "run")
    assert "GIT_INDEX_FILE" not in run


def test_copy_mode_creates_a_self_removing_container(sandbox):
    repo, env, log = sandbox
    env["LEAF_COPY_REPO"] = "1"
    r = subprocess.run(["./run.sh", "leaks"], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (create,) = _engine_calls(log, "create")
    assert create.startswith("create --rm ")
    assert _engine_calls(log, "start") == ["start -a fake-container"]


def test_build_output_is_hidden_and_a_first_build_says_so(sandbox):
    repo, env, log = sandbox
    r = subprocess.run(["./run.sh", "leaks"], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "debconf" not in r.stdout + r.stderr
    assert "Building the leaf-tools image (first time" in r.stderr
    (build,) = _engine_calls(log, "build")
    assert "-t leaf-tools -f Containerfile.tools" in build
    (run,) = _engine_calls(log, "run")
    assert " fake-image gitleaks " in run


def test_every_build_names_its_containerfile(sandbox):
    # docker, unlike podman, looks only for a Dockerfile when -f is missing.
    repo, env, log = sandbox
    for check, file in (("test", "Containerfile.tools"), ("e2e", "Containerfile.tools"),
                        ("screenshots", "Containerfile.tools")):
        log.unlink(missing_ok=True)
        r = subprocess.run(["./run.sh", check], cwd=repo, env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        (build,) = _engine_calls(log, "build")
        assert f" -f {file} " in build


def test_tests_run_in_parallel(sandbox):
    # pytest-xdist: a worker per CPU for the unit tests, one per two CPUs
    # for the browser tests (each worker runs a Chromium).
    repo, env, log = sandbox
    r = subprocess.run(["./run.sh", "test"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    run = log.read_text()   # the script spans lines
    assert "-n auto" in run
    log.unlink()
    r = subprocess.run(["./run.sh", "e2e"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    run = log.read_text()
    assert "$(nproc) / 2" in run and '-n "$n"' in run


def test_test_checks_the_server_then_runs_pytest_against_it(sandbox):
    repo, env, log = sandbox
    r = subprocess.run(["./run.sh", "test", "tests/test_routes.py", "-k", "Auth"], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (run,) = [l for l in log.read_text().split("\nrun ") if "go test" in l]
    assert "go vet ./..." in run and "--ignore=tests/e2e" in run
    assert run.rstrip().endswith("tests/test_routes.py -k Auth")


def test_a_failed_build_shows_its_output_and_runs_nothing(sandbox):
    repo, env, log = sandbox
    env["FAKE_BUILD_FAILS"] = "1"
    r = subprocess.run(["./run.sh", "leaks"], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "debconf: noise a RUN step prints" in r.stderr
    assert not _engine_calls(log, "run")


def _no_local_gitleaks(env):
    return shutil.which("gitleaks", path=env["PATH"]) is None


@pytest.mark.parametrize("commit_args", [["-a"], ["notes.txt"]])
def test_hook_scans_the_temporary_index_of_commit_a_and_commit_path(sandbox, commit_args):
    """The regression: commit -a / commit <path> stage into a temporary index,
    which the scan in the container must get, or it sees nothing."""
    repo, env, log = sandbox
    if not _no_local_gitleaks(env):
        pytest.skip("a local gitleaks takes the hook's other path")
    (repo / "notes.txt").write_text("two\n")
    r = _git(repo, "commit", "-q", "-m", "change", *commit_args, env=env)
    assert r.returncode == 0, r.stderr
    (run,) = _engine_calls(log, "run")
    assert "-e GIT_INDEX_FILE=/repo/.git/" in run
    assert "--pre-commit --staged" in run


def test_hook_refuses_a_leak_and_only_warns_when_the_scan_fails(sandbox):
    repo, env, log = sandbox
    if not _no_local_gitleaks(env):
        pytest.skip("a local gitleaks takes the hook's other path")
    (repo / "notes.txt").write_text("two\n")
    _git(repo, "add", "notes.txt")
    env["FAKE_EXIT"] = "3"
    r = _git(repo, "commit", "-q", "-m", "leak", env=env)
    assert r.returncode != 0
    assert "commit refused" in r.stderr
    env["FAKE_EXIT"] = "125"
    r = _git(repo, "commit", "-q", "-m", "engine broken", env=env)
    assert r.returncode == 0, r.stderr
    assert "didn't run (exit 125)" in r.stderr


def test_eval_mounts_the_wiki_read_only_and_builds_with_the_model(sandbox, tmp_path):
    repo, env, log = sandbox
    wiki = tmp_path / "wiki"
    (wiki / "_eval").mkdir(parents=True)
    (repo / ".env").write_text(f"LEAF_DATA={wiki}\nLEAF_SEMANTIC_SEARCH=on\n")
    r = subprocess.run(["./run.sh", "eval"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "No questions at" in r.stderr
    (wiki / "_eval" / "retrieval.json").write_text("[]")
    r = subprocess.run(["./run.sh", "eval", "_eval/retrieval.json", "-k", "5"],
                       cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (build,) = _engine_calls(log, "build")
    assert "--build-arg LEAF_SEMANTIC_SEARCH=on" in build
    (run,) = _engine_calls(log, "run")
    assert f"-v {wiki}:/wiki-data:ro,z -e LEAF_DATA=/wiki-data" in run
    assert "-e LEAF_SEMANTIC_SEARCH=on " in run
    assert run.endswith("/app/leaf eval /wiki-data/_eval/retrieval.json -k 5")


def test_user_and_config_run_leaf_in_a_one_off_container(sandbox, tmp_path):
    """./run.sh user …/config … run the binary through compose, with .env
    and the wiki as up has them; -T when stdin isn't a terminal."""
    repo, env, log = sandbox
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (repo / ".env").write_text(f"LEAF_DATA={wiki}\n")
    for args in (["user", "add", "alice", "--mcp-token"], ["config", "check"]):
        r = subprocess.run(["./run.sh", *args], cwd=repo, env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        call = [l for l in _engine_calls(log, "compose") if " run " in l][-1]
        assert call.endswith("run --rm --no-deps -T leaf /app/leaf " + " ".join(args))
        assert [l for l in _engine_calls(log, "compose") if " build -q leaf" in l]
    (repo / ".env").write_text(f"LEAF_DATA={wiki}-missing\n")
    r = subprocess.run(["./run.sh", "user", "list"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "No wiki at" in r.stderr
