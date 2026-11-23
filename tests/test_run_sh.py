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
  compose) echo "env ZONE_BIND=$ZONE_BIND ZONE_UID=$ZONE_UID ZONE_GID=$ZONE_GID ZONE_BUILD=$ZONE_BUILD" >> "$FAKE_LOG" ;;
  create) echo fake-container ;;
  ps) [ -z "$FAKE_PS" ] || echo "$FAKE_PS" ;;
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
    """A git repo with run.sh and the hook, and an env whose engine is the stub.
    A checkout to run.sh: the Containerfile and the compose files beside it."""
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    for name in ("run.sh", "Containerfile", "compose.yaml", "compose.build.yaml", "compose.podman.yaml"):
        shutil.copy(REPO / name, repo / name)
    shutil.copy(REPO / ".githooks" / "pre-commit", repo / ".githooks" / "pre-commit")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    engine = bin_dir / "podman"
    engine.write_text(FAKE_ENGINE)
    engine.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "ZONE_"))}
    # Without a local gitleaks where it has a folder of its own (the tools
    # image): the hook then takes the engine, which is what is tested here.
    path = [d for d in env.get("PATH", "").split(os.pathsep)
            if d and not (os.path.exists(os.path.join(d, "gitleaks")) and not os.path.exists(os.path.join(d, "bash")))]
    env["PATH"] = os.pathsep.join([str(bin_dir), *path])
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


@pytest.fixture
def worktree(sandbox):
    """A git worktree of the sandbox's repo, with run.sh and the hook in it."""
    repo, env, log = sandbox
    _git(repo, "add", "run.sh", ".githooks")
    _git(repo, "commit", "-q", "--no-verify", "-m", "the scripts")
    tree = repo.parent / "tree"
    assert _git(repo, "worktree", "add", "-q", str(tree), "-b", "feature").returncode == 0
    return repo, tree, env, log


def test_leaks_in_a_worktree_mounts_the_repos_git_folder(worktree):
    """.git is a file there, naming the repo's .git by its host path: the
    container gets that folder at the same path, read-only."""
    repo, tree, env, log = worktree
    r = subprocess.run(["./run.sh", "leaks"], cwd=tree, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (run,) = _engine_calls(log, "run")
    git_dir = (repo / ".git").resolve()
    assert f"-v {git_dir}:{git_dir}:ro,z " in run and f"-v {tree.resolve()}:/repo:z" in run
    assert "GIT_INDEX_FILE" not in run


def test_leaks_in_the_repo_itself_mounts_nothing_more(sandbox):
    repo, env, log = sandbox
    r = subprocess.run(["./run.sh", "leaks"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (run,) = _engine_calls(log, "run")
    assert run.count(" -v ") == 1


def test_leaks_in_a_worktree_passes_its_index_by_its_own_path(worktree, tmp_path):
    repo, tree, env, log = worktree
    index = (repo / ".git").resolve() / "worktrees" / "tree" / "index.lock"
    env["GIT_INDEX_FILE"] = str(index)
    r = subprocess.run(["./run.sh", "leaks", "--pre-commit", "--staged"], cwd=tree, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (run,) = _engine_calls(log, "run")
    assert f"-e GIT_INDEX_FILE={index} " in run
    # One in neither place still can't be reached.
    log.unlink()
    env["GIT_INDEX_FILE"] = str(tmp_path / "elsewhere.lock")
    r = subprocess.run(["./run.sh", "leaks"], cwd=tree, env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "outside the repo" in r.stderr and not log.exists()


@pytest.mark.parametrize("commit_args", [[], ["-a"], ["notes.txt"]])
def test_hook_scans_a_commit_in_a_worktree(worktree, commit_args):
    """The regression: in a worktree the scan didn't run (exit 2), and the
    commit went in unchecked."""
    repo, tree, env, log = worktree
    if not _no_local_gitleaks(env):
        pytest.skip("a local gitleaks takes the hook's other path")
    (tree / "notes.txt").write_text("two\n")
    if not commit_args:
        _git(tree, "add", "notes.txt")
    r = _git(tree, "commit", "-q", "-m", "change", *commit_args, env=env)
    assert r.returncode == 0 and "didn't run" not in r.stderr, r.stderr
    (run,) = _engine_calls(log, "run")
    git_dir = (repo / ".git").resolve()
    assert f"-v {git_dir}:{git_dir}:ro,z " in run and "--pre-commit --staged" in run
    if commit_args:   # a temporary index, beside the worktree's own
        assert f"-e GIT_INDEX_FILE={git_dir}/worktrees/tree/" in run
    env["FAKE_EXIT"] = "3"
    (tree / "notes.txt").write_text("three\n")
    r = _git(tree, "commit", "-q", "-m", "leak", "-a", env=env)
    assert r.returncode != 0 and "commit refused" in r.stderr


def test_copy_mode_creates_a_self_removing_container(sandbox):
    repo, env, log = sandbox
    env["ZONE_COPY_REPO"] = "1"
    r = subprocess.run(["./run.sh", "leaks"], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (create,) = _engine_calls(log, "create")
    assert create.startswith("create --rm ")
    assert _engine_calls(log, "start") == ["start -a fake-container"]


def test_a_ci_run_removes_what_a_cancelled_run_left(sandbox):
    """A cancelled job's tests go on in their container (the runner kills
    only the job's own): the next run of the repository removes them, and
    leaves its own run's and another repository's alone."""
    repo, env, log = sandbox
    env.update(ZONE_COPY_REPO="1", GITHUB_RUN_ID="8", GITHUB_REPOSITORY="me/zone",
               FAKE_PS="old1 me/zone#7\nmine me/zone#8\nold2 me/zone#6\nother you/zone#7\nlong me/zone#81")
    r = subprocess.run(["./run.sh", "leaks"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "an earlier run left behind" in r.stderr
    assert _engine_calls(log, "rm") == ["rm -f old1 old2 long", "rm -f fake-container"]
    (create,) = _engine_calls(log, "create")
    assert "--label zonewiki-ci-run=me/zone#8 " in create
    # Nothing left: nothing removed, nothing said. Outside CI: no label, no look.
    for change, labelled in (({"FAKE_PS": "mine me/zone#8"}, True), ({"GITHUB_RUN_ID": ""}, False)):
        log.unlink()
        r = subprocess.run(["./run.sh", "leaks"], cwd=repo, env={**env, **change}, capture_output=True, text=True)
        assert r.returncode == 0 and "left behind" not in r.stderr, r.stderr
        assert _engine_calls(log, "rm") == ["rm -f fake-container"]
        assert ("--label zonewiki-ci-run" in _engine_calls(log, "create")[0]) == labelled
        assert bool(_engine_calls(log, "ps")) == labelled


def test_build_output_is_hidden_and_a_first_build_says_so(sandbox):
    repo, env, log = sandbox
    r = subprocess.run(["./run.sh", "leaks"], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "debconf" not in r.stdout + r.stderr
    assert "Building the zonewiki-tools image from Containerfile.tools (first time" in r.stderr
    assert "Scanning the git history for secrets" in r.stderr
    (build,) = _engine_calls(log, "build")
    assert "-t zonewiki-tools -f Containerfile.tools" in build
    (run,) = _engine_calls(log, "run")
    assert " fake-image gitleaks " in run


def test_binaries_say_what_they_build_and_where_it_lands(sandbox):
    repo, env, log = sandbox
    r = subprocess.run(["./run.sh", "binaries"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "Building the release binaries in the tools image" in r.stderr
    assert f"The binaries are in {repo.resolve()}/dist/." in r.stderr
    run = log.read_text()   # the script spans lines; each target is named as it builds
    assert 'echo "==> dist/$name"' in run
    # Copied into the container (CI), nothing comes back, and it says so.
    env["ZONE_COPY_REPO"] = "1"
    r = subprocess.run(["./run.sh", "binaries"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "nothing came back to dist/" in r.stderr and "The binaries are in" not in r.stderr


def test_each_command_says_what_it_does(sandbox, tmp_path):
    repo, env, log = sandbox
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (repo / ".env").write_text(f"ZONE_DATA={wiki}\n")
    for args, said in ((["up"], "the binary is compiled inside it, not into this folder"),
                       (["build"], "./run.sh binaries builds binaries into dist/"),
                       (["down"], "Stopping Zonewiki"),
                       (["test"], "gofmt, go vet, go test -race, then pytest"),
                       (["e2e"], "browser and tasks-core tests"),
                       (["bench"], "tests/bench_tasks.py"),
                       (["retrieval"], "Measuring search on tests/retrieval/"),
                       (["screenshots"], "docs/screenshots/"),
                       (["config", "check"], "Building Zonewiki's image")):
        r = subprocess.run(["./run.sh", *args], cwd=repo, env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        assert said in r.stderr, (args, r.stderr)
        # Never on stdout: build_image's is the image ID the commands run.
        assert "==>" not in r.stdout


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
    assert "gofmt -l ." in run and "go vet ./..." in run and "go test -race ./..." in run and "--ignore=tests/e2e" in run
    assert run.rstrip().endswith("tests/test_routes.py -k Auth")


def test_flaky_repeats_the_chosen_browser_tests_on_a_busy_machine(sandbox):
    """./run.sh flaky [N] [pytest args]: the browser tests N times each (20
    without a number), a worker per CPU and busy loops beside them."""
    repo, env, log = sandbox
    for args, repeat in ((["flaky", "50", "-k", "one_line"], "50"), (["flaky", "-k", "one_line"], "20")):
        log.unlink(missing_ok=True)
        r = subprocess.run(["./run.sh", *args], cwd=repo, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert f"{repeat} times each on a busy machine" in r.stderr
        (run,) = [l for l in log.read_text().split("\nrun ") if "pytest tests/e2e" in l]
        assert f"-e ZONE_E2E_REPEAT={repeat}" in run and '-n "$cpus"' in run and "while :; do :; done" in run
        assert "tests/test_tasks_core.py" not in run and run.rstrip().endswith("-k one_line")


def test_bench_measures_then_runs_under_the_race_detector(sandbox):
    repo, env, log = sandbox
    r = subprocess.run(["./run.sh", "bench"], cwd=repo, env={**env, "ZONE_BENCH_SLACK": "3"}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (run,) = [l for l in log.read_text().split("\nrun ") if "bench_tasks.py" in l]
    assert "-e ZONE_BENCH_SLACK=3" in run and run.count("tests/bench_tasks.py") == 2
    assert "ZONE_TEST_RACE=1 python3 -m pytest" in run and "-n 0 -s tests/bench_tasks.py" in run


def test_retrieval_measures_the_sample_wiki_in_the_apps_image_with_the_model(sandbox):
    repo, env, log = sandbox
    r = subprocess.run(["./run.sh", "retrieval", "--k", "5"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    logged = log.read_text()
    (build,) = [l for l in logged.split("\n") if l.startswith("build ") and "zonewiki-retrieval" in l]
    assert "-f Containerfile " in build and "--build-arg ZONE_SEMANTIC_SEARCH=on" in build
    (run,) = [l for l in logged.split("\nrun ") if "zonewiki eval" in l]
    assert "-e ZONE_DATA=/repo/tests/retrieval/wiki" in run and "-e ZONE_SEMANTIC_SEARCH=on" in run
    assert run.rstrip().endswith("/app/zonewiki eval --floor /repo/tests/retrieval/floors.json /repo/tests/retrieval/questions.json --k 5")


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
    (repo / ".env").write_text(f"ZONE_DATA={wiki}\nZONE_SEMANTIC_SEARCH=on\n")
    r = subprocess.run(["./run.sh", "eval"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "No questions at" in r.stderr
    (wiki / "_eval" / "retrieval.json").write_text("[]")
    r = subprocess.run(["./run.sh", "eval", "_eval/retrieval.json", "--k", "5"],
                       cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (build,) = _engine_calls(log, "build")
    assert "--build-arg ZONE_SEMANTIC_SEARCH=on" in build
    (run,) = _engine_calls(log, "run")
    assert f"-v {wiki}:/wiki-data:ro,z -e ZONE_DATA=/wiki-data" in run
    assert "-e ZONE_SEMANTIC_SEARCH=on " in run
    assert run.endswith("/app/zonewiki eval /wiki-data/_eval/retrieval.json --k 5")


def test_user_and_config_run_zonewiki_in_a_one_off_container(sandbox, tmp_path):
    """./run.sh user …/group …/config … run the binary through compose, with .env
    and the wiki as up has them; -T when stdin isn't a terminal."""
    repo, env, log = sandbox
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (repo / ".env").write_text(f"ZONE_DATA={wiki}\n")
    for args in (["user", "add", "alice", "--mcp-token"], ["group", "list"], ["config", "check"]):
        r = subprocess.run(["./run.sh", *args], cwd=repo, env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        call = [l for l in _engine_calls(log, "compose") if " run " in l][-1]
        assert call.endswith("run --rm --no-deps -T zonewiki /app/zonewiki " + " ".join(args))
        assert [l for l in _engine_calls(log, "compose") if " build -q zonewiki" in l]
    (repo / ".env").write_text(f"ZONE_DATA={wiki}-missing\n")
    r = subprocess.run(["./run.sh", "user", "list"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "No wiki at" in r.stderr


def test_the_default_wiki_is_zonewiki_in_the_home_folder(sandbox, tmp_path):
    repo, env, log = sandbox
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home" / "zonewiki").mkdir(parents=True)
    (repo / ".env").write_text("TZ=UTC\n")
    r = subprocess.run(["./run.sh", "up"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and f"Zonewiki data: {tmp_path / 'home' / 'zonewiki'}" in r.stdout, r.stderr


def test_pre_push_runs_the_clones_own_hook(worktree):
    """core.hooksPath has git look only in .githooks/: the pre-push hook
    there runs .git/hooks/pre-push, from a worktree too, with its arguments
    and input, and its refusal refuses the push."""
    repo, tree, env, log = worktree
    shutil.copy(REPO / ".githooks" / "pre-push", tree / ".githooks" / "pre-push")
    remote = repo.parent / "remote.git"
    assert _git(repo, "init", "-q", "--bare", str(remote)).returncode == 0
    _git(tree, "remote", "add", "out", str(remote))
    # Without a hook of its own the push goes through.
    assert _git(tree, "push", "-q", "out", "feature", env=env).returncode == 0
    seen = repo.parent / "seen.txt"
    own = repo / ".git" / "hooks" / "pre-push"
    own.write_text(f'#!/bin/sh\n{{ echo "$1 $2"; cat; }} > "{seen}"\nexit "${{REFUSE:-0}}"\n')
    own.chmod(0o755)
    (tree / "notes.txt").write_text("pushed\n")
    _git(tree, "commit", "-q", "--no-verify", "-am", "more")
    head = _git(tree, "rev-parse", "HEAD").stdout.strip()
    r = _git(tree, "push", "-q", "out", "feature", env={**env, "REFUSE": "1"})
    assert r.returncode != 0
    assert _git(remote, "rev-parse", "feature").stdout.strip() != head
    first, refs = seen.read_text().split("\n", 1)
    assert first == f"out {remote}" and f"refs/heads/feature {head} refs/heads/feature" in refs
    assert _git(tree, "push", "-q", "out", "feature", env=env).returncode == 0
    assert _git(remote, "rev-parse", "feature").stdout.strip() == head


def test_the_other_own_hooks_run_too(worktree):
    """post-commit and post-rewrite are delegated the same way: a commit runs
    the clone's own post-commit, an amend its post-rewrite (the kind as its
    argument); a hook of the clone that isn't executable is skipped."""
    repo, tree, env, log = worktree
    for name in ("pre-push", "post-commit", "post-rewrite"):
        assert (REPO / ".githooks" / name).read_bytes() == (REPO / ".githooks" / "pre-push").read_bytes()
        shutil.copy(REPO / ".githooks" / name, tree / ".githooks" / name)
        (tree / ".githooks" / name).chmod(0o755)
    seen = repo.parent / "seen"
    for name in ("post-commit", "post-rewrite"):
        own = repo / ".git" / "hooks" / name
        own.write_text(f'#!/bin/sh\n{{ echo "$@"; cat; }} >> "{seen}-{name}"\n')
    (repo / ".git" / "hooks" / "post-commit").chmod(0o755)   # post-rewrite stays unexecutable
    (tree / "notes.txt").write_text("changed\n")
    assert _git(tree, "commit", "-q", "--no-verify", "-am", "more", env=env).returncode == 0
    assert (repo.parent / "seen-post-commit").exists() and not (repo.parent / "seen-post-rewrite").exists()
    (repo / ".git" / "hooks" / "post-rewrite").chmod(0o755)
    assert _git(tree, "commit", "-q", "--no-verify", "--amend", "-m", "again", env=env).returncode == 0
    assert (repo.parent / "seen-post-rewrite").read_text().split("\n")[0] == "amend"


@pytest.fixture
def beside_compose(sandbox, tmp_path):
    """run.sh downloaded on its own, next to compose.yaml (the prebuilt image),
    with a wiki and its .env: no checkout, no git."""
    _, env, log = sandbox
    folder = tmp_path / "zone"
    folder.mkdir()
    for name in ("run.sh", "compose.yaml", "compose.podman.yaml"):
        shutil.copy(REPO / name, folder / name)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (folder / ".env").write_text(f"ZONE_DATA={wiki}\n")
    return folder, env, log


def _compose_calls(log, what):
    return [l for l in _engine_calls(log, "compose") if " version" not in l and what in l]


def test_beside_compose_yaml_up_runs_the_prebuilt_image(beside_compose):
    folder, env, log = beside_compose
    r = subprocess.run(["./run.sh", "up"], cwd=folder, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "from the prebuilt image" in r.stderr
    (up,) = _compose_calls(log, " up ")
    assert up.startswith("compose -f compose.yaml ") and up.endswith(" up -d")
    assert "compose.build.yaml" not in log.read_text() and not _engine_calls(log, "build")


def test_in_a_checkout_up_builds_from_it(sandbox, tmp_path):
    repo, env, log = sandbox
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (repo / ".env").write_text(f"ZONE_DATA={wiki}\n")
    r = subprocess.run(["./run.sh", "up"], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "from this checkout" in r.stderr
    (up,) = _compose_calls(log, " up ")
    assert up.startswith("compose -f compose.build.yaml ") and up.endswith(" up -d --build")


def test_beside_compose_yaml_the_checkouts_commands_say_they_need_one(beside_compose):
    folder, env, log = beside_compose
    for cmd in ("build", "test", "e2e", "flaky", "bench", "retrieval", "binaries", "leaks", "screenshots", "eval"):
        r = subprocess.run(["./run.sh", cmd], cwd=folder, env=env, capture_output=True, text=True)
        assert r.returncode == 1 and "needs a checkout of Zonewiki" in r.stderr, (cmd, r.stderr)
    assert not log.exists() or not [l for l in log.read_text().splitlines()
                                    if not l.startswith(("compose version", "info ", "env "))]


def test_beside_compose_yaml_the_settings_commands_build_nothing(beside_compose):
    folder, env, log = beside_compose
    r = subprocess.run(["./run.sh", "user", "add", "alice"], cwd=folder, env=env, capture_output=True,
                       text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert not _compose_calls(log, " build ")
    (run,) = _compose_calls(log, " run ")
    assert run.startswith("compose -f compose.yaml ") and run.endswith("zonewiki /app/zonewiki user add alice")


def test_update_pulls_the_image_or_the_code_then_starts_again(beside_compose, sandbox, tmp_path):
    folder, env, log = beside_compose
    r = subprocess.run(["./run.sh", "update"], cwd=folder, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    calls = [l for l in _compose_calls(log, "") if l.endswith((" pull", " up -d"))]
    assert len(calls) == 2 and calls[0].endswith(" pull") and calls[1].endswith(" up -d"), calls
    assert calls[0].startswith("compose -f compose.yaml ")
    # A clone of the checkout: git pull, then the image built again.
    repo, _, _ = sandbox
    clone = tmp_path / "clone"
    assert _git(tmp_path, "clone", "-q", str(repo), str(clone)).returncode == 0
    (clone / ".env").write_text(f"ZONE_DATA={tmp_path / 'wiki'}\n")
    log.unlink()
    r = subprocess.run(["./run.sh", "update"], cwd=clone, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "git pull" in r.stderr
    (up,) = _compose_calls(log, " up ")
    assert up.endswith(" up -d --build") and not _compose_calls(log, " pull")


def test_env_names_the_address_and_the_user_unless_remote_is_allowed(beside_compose, tmp_path):
    # compose without run.sh takes ZONE_BIND/ZONE_UID from .env; run.sh's
    # exports would win over them, so it reads them itself.
    folder, env, log = beside_compose
    wiki = tmp_path / "wiki"
    (folder / ".env").write_text(f"ZONE_DATA={wiki}\nZONE_BIND=0.0.0.0\nZONE_UID=1026\nZONE_GID=100\nZONE_PORT=5080\n")
    r = subprocess.run(["./run.sh", "up"], cwd=folder, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "ZONE_BIND=0.0.0.0 ZONE_UID=1026 ZONE_GID=100 " in log.read_text()
    assert "Zonewiki running at http://localhost:5080" in r.stdout
    log.unlink()
    (folder / ".env").write_text(f"ZONE_DATA={wiki}\n")
    r = subprocess.run(["./run.sh", "up"], cwd=folder, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert f"ZONE_BIND=127.0.0.1 ZONE_UID={os.getuid()} " in log.read_text()
    log.unlink()
    (folder / ".env").write_text(f"ZONE_DATA={wiki}\nZONE_ALLOW_REMOTE=1\n")
    r = subprocess.run(["./run.sh", "up"], cwd=folder, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "ZONE_BIND=0.0.0.0 " in log.read_text()


def test_run_sh_alone_says_what_it_needs(tmp_path, sandbox):
    _, env, _ = sandbox
    shutil.copy(REPO / "run.sh", tmp_path / "run.sh")
    r = subprocess.run(["./run.sh", "up"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "needs compose.yaml beside it" in r.stderr
