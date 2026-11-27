"""The Containerfiles: pins that must agree with each other and with the
code, and the layering that keeps a code change from refetching the model."""

import hashlib
import re
import subprocess

import pytest

from tests.settings import REPO

CONTAINERFILE = (REPO / "Containerfile").read_text(encoding="utf-8")
TOOLS = (REPO / "Containerfile.tools").read_text(encoding="utf-8")


def args(text: str) -> dict[str, str]:
    return dict(re.findall(r"^ARG (\w+)=(\S*)", text, re.M))


def stage(name: str) -> str:
    start = CONTAINERFILE.index(f" AS {name}\n")
    end = CONTAINERFILE.find("\nFROM ", start)
    return CONTAINERFILE[start:end]


def test_the_model_is_the_one_the_server_checks():
    embed = (REPO / "go" / "embed.go").read_text(encoding="utf-8")
    a = args(stage("semantic"))
    assert f'defaultModel  = "{a["MODEL"]}"' in embed
    assert f'modelRevision = "{a["MODEL_REVISION"]}"' in embed
    assert f'{{"model.safetensors", "{a["MODEL_WEIGHTS_SHA256"]}"}}' in embed
    # The weights as the model's authors publish them (float16), not the
    # ONNX export of them, twice the size for the same numbers.
    assert f'"$base/model.safetensors"' in stage("semantic") and "onnx" not in stage("semantic")
    assert f'{{"tokenizer.json", "{a["MODEL_VOCAB_SHA256"]}"}}' in embed


def test_the_semantic_layer_takes_nothing_from_the_repo():
    # Else every change to the code would download the model again.
    assert "COPY" not in stage("semantic")


def test_base_images_and_ci_actions_are_pinned_by_hash():
    # A tag can be moved to other content: what builds and publishes Zonewiki
    # names the exact image (its digest) and the exact commit of an action.
    bases = dict(re.findall(r"^ARG (UBUNTU)=(\S+)", CONTAINERFILE, re.M))
    froms = re.findall(r"^FROM (\S+)", CONTAINERFILE + TOOLS, re.M)
    assert froms.count("${UBUNTU}") == 3 and len(froms) == 4
    for image in [bases["UBUNTU"]] + [f for f in froms if f != "${UBUNTU}"]:
        assert re.fullmatch(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}", image), image
    # Every workflow, whatever it is called: a new one is held to the same.
    workflows = [p for d in (".github", ".forgejo") for p in sorted((REPO / d / "workflows").glob("*.yml"))]
    assert {p.name for p in workflows} >= {"ci.yml", "codeql.yml"}
    ci = "".join(p.read_text(encoding="utf-8") for p in workflows)
    for image in re.findall(r"^\s+image: (\S+)$", ci, re.M):   # a job's container (Forgejo)
        assert re.fullmatch(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}", image), image
    uses = re.findall(r"uses: (\S+)(.*)", ci)
    assert len(uses) > 10
    for action, comment in uses:
        # owner/repo, maybe a folder of it (github/codeql-action/init).
        assert re.fullmatch(r"[\w-]+/[\w-]+(/[\w-]+)*@[0-9a-f]{40}", action), action
        assert re.fullmatch(r" # v\d+\.\d+\.\d+", comment), action   # the release it is


def test_codeql_is_told_of_checks_that_exist():
    """The functions .github/codeql/extensions/ calls Zonewiki's checks are in
    the code under those names: CodeQL says nothing of a row that matches
    no function, it only reports again what the check had settled. And the
    workflow reads the config that leaves the tests out."""
    ext = REPO / ".github" / "codeql" / "extensions" / "zone-go"
    pack = (ext / "codeql-pack.yml").read_text(encoding="utf-8")
    assert "codeql/go-all" in pack and "- models.yml" in pack
    rows = re.findall(r'^\s+- \["([\w/]+)", "(\w*)", False, "(\w+)", "", "", "[\w\[\]]+", (?:"true", )?"([\w-]+)", "manual"\]$',
                      (ext / "models.yml").read_text(encoding="utf-8"), re.M)
    assert len(rows) >= 9
    for package, typ, func, kind in rows:
        assert kind in ("path-injection", "html-injection", "url-redirection"), func
        assert package == "zonewiki" or package.startswith("zonewiki/internal/"), package
        folder = REPO / "go" / package.removeprefix("zonewiki").lstrip("/")
        source = "".join(p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.go")) if not p.name.endswith("_test.go"))
        decl = rf"^func \(\w+ \*?{typ}\) {func}\(" if typ else rf"^func {func}\("
        assert re.search(decl, source, re.M), f"{package}: no {typ + '.' if typ else ''}{func}"
    workflow = (REPO / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")
    assert "config-file: ./.github/codeql/codeql-config.yml" in workflow
    config = (REPO / ".github" / "codeql" / "codeql-config.yml").read_text(encoding="utf-8")
    assert "\npaths-ignore:\n  - tests\n" in config


def test_both_images_build_with_the_same_go():
    for key in ("GO_VERSION", "GO_SHA256_AMD64", "GO_SHA256_ARM64"):
        assert args(stage("build"))[key] == args(TOOLS)[key], key
    for gomod in (REPO / "go" / "go.mod", REPO / "go.mod"):
        assert f"\ngo {args(TOOLS)['GO_VERSION']}\n" in gomod.read_text(encoding="utf-8"), gomod


def test_the_binary_is_built_with_zones_own_files():
    # zonefiles.go builds the browser code, the docs (Help, zone_help) and the
    # licenses into the binary: the build stage copies what its go:embed line
    # names, and the image serves the binary's copies, with none beside it.
    embed = re.search(r"^//go:embed (.+)$", (REPO / "zonefiles.go").read_text(encoding="utf-8"), re.M).group(1).split()
    assert embed == ["public", "README.md", "docs/manual/*.md", "docs/spec/*.md", "docs/screenshots/*.png",
                     "LICENSE", "THIRD_PARTY_LICENSES"]
    build = stage("build")
    for line in ("COPY zonefiles.go README.md LICENSE THIRD_PARTY_LICENSES /src/", "COPY public/ /src/public/",
                 "COPY docs/manual/ /src/docs/manual/", "COPY docs/spec/ /src/docs/spec/",
                 "COPY docs/screenshots/*.png /src/docs/screenshots/"):
        assert line in build, line
    final = CONTAINERFILE[CONTAINERFILE.rindex("\nFROM "):]
    assert "COPY public/" not in final and "COPY docs/" not in final


def test_the_root_module_is_there_for_go_mod_download():
    # go/go.mod takes the module of Zonewiki's own files from ../ (replace): both
    # images fetch the modules with the root's go.mod beside go/.
    assert "require zonefiles v0.0.0\n\nreplace zonefiles => ../\n" in (REPO / "go" / "go.mod").read_text(encoding="utf-8")
    assert re.search(r"^module zonefiles$", (REPO / "go.mod").read_text(encoding="utf-8"), re.M)
    build = stage("build")
    assert build.index("COPY go.mod /src/") < build.index("RUN go mod download") < build.index("COPY go/ ./")
    assert "COPY go.mod /tmp/gomod/\nCOPY go/go.mod go/go.sum /tmp/gomod/go/\nRUN cd /tmp/gomod/go && go mod download" in TOOLS


def test_the_image_ships_the_licenses():
    final = CONTAINERFILE[CONTAINERFILE.rindex("\nFROM "):]
    assert "COPY LICENSE THIRD_PARTY_LICENSES ./" in final


def test_zones_license_is_the_agpl():
    assert (REPO / "LICENSE").read_text(encoding="utf-8").lstrip().startswith("GNU AFFERO GENERAL PUBLIC LICENSE")
    assert "org.opencontainers.image.licenses=AGPL-3.0-or-later" in (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_the_notices_name_what_is_pinned():
    # Raising a pin without its license fails here.
    notices = (REPO / "THIRD_PARTY_LICENSES").read_text(encoding="utf-8")
    gomod = (REPO / "go" / "go.mod").read_text(encoding="utf-8")
    modules = re.findall(r"^\t(\S+) (v\S+)", gomod, re.M)
    assert modules
    for module, version in modules:
        assert f"{module} {version}\n" in notices
    assert f"Go {args(TOOLS)['GO_VERSION']} " in notices
    assert args(stage("semantic"))["MODEL_REVISION"] in notices


def test_every_vendored_library_has_its_license():
    folders = [p for p in (REPO / "public" / "vendor").iterdir() if p.is_dir()]
    assert folders
    for folder in folders:
        assert (folder / "LICENSE").is_file(), folder.name


def test_mermaids_notices_are_for_the_vendored_bundle():
    # Updating mermaid.min.js without docs/licenses/mermaid_notices.py fails here.
    folder = REPO / "public" / "vendor" / "mermaid"
    notices = (folder / "THIRD_PARTY_LICENSES").read_text(encoding="utf-8")
    assert hashlib.sha256((folder / "mermaid.min.js").read_bytes()).hexdigest() in notices
    # elkjs is the one copyleft package in the bundle: its GPL option is what lets
    # Zonewiki ship it under the AGPL, and both licenses ask for a way to get the source.
    assert "License: EPL-2.0 OR GPL-3.0-or-later" in notices
    assert "https://github.com/kieler/elkjs" in notices
    assert "GNU GENERAL PUBLIC LICENSE" in (folder / "COPYING.GPL-3.0").read_text(encoding="utf-8")


def test_the_binary_is_zonewiki():
    final = CONTAINERFILE[CONTAINERFILE.rindex("\nFROM "):]
    assert "COPY --from=build /zonewiki ./zonewiki" in final
    # Every interface in the container, for its network bridge: the binary's
    # default port alone would be the container's localhost, out of reach.
    assert 'CMD ["/app/zonewiki", "--addr", "0.0.0.0:5080"]' in final
    for compose in ("compose.build.yaml", "compose.yaml"):
        assert '"/app/zonewiki", "health"' in (REPO / compose).read_text(encoding="utf-8")


def test_the_prebuilt_image_stays_on_localhost_until_opened():
    # compose.yaml is the manual's first way into a container, on a
    # laptop too: its port is loopback's, as compose.build.yaml's, until .env says
    # ZONE_BIND=0.0.0.0, and Zonewiki is told the same address (First run asks
    # for a login once it is on the network). It pulls, never builds.
    compose = (REPO / "compose.yaml").read_text(encoding="utf-8")
    body = "\n".join(l for l in compose.splitlines() if not l.lstrip().startswith("#"))
    assert '- "${ZONE_BIND:-127.0.0.1}:${ZONE_PORT:-5080}:5080"' in body
    assert "ZONE_BIND: ${ZONE_BIND:-127.0.0.1}" in body
    assert "image: ghcr.io/zonewiki/zonewiki:latest" in body and "build:" not in body
    assert "0.0.0.0" not in body


def test_ci_publishes_semantic_search_and_measures_it():
    # The image CI publishes has the model, and the retrieval job, which
    # runs it against the floors, is on (both were off from 2026-11-18 to
    # 2026-11-26, while onnxruntime ran the model and its source was gone).
    for path in (".github/workflows/ci.yml", ".forgejo/workflows/ci.yml"):
        ci = (REPO / path).read_text(encoding="utf-8")
        assert "ZONE_SEMANTIC_SEARCH=on" in ci and "ZONE_SEMANTIC_SEARCH=off" not in ci, path
        job = ci[ci.index("\n  retrieval:\n"):]
        job = job[:job.index("\n  image:\n")]
        assert "if: false" not in job and "./run.sh retrieval" in job, path


def test_nothing_runs_the_model_but_the_binary():
    # Semantic search's model runs in Go (go/internal/encoder): no runtime
    # is downloaded into an image or linked into the binary, which is built
    # without cgo and with Go's simd experiment, in both images alike.
    assert "onnxruntime" not in CONTAINERFILE.lower() and "onnxruntime" not in TOOLS.lower()
    assert "onnxruntime" not in (REPO / "go" / "go.mod").read_text(encoding="utf-8")
    env = "ENV PATH=/usr/local/go/bin:$PATH GOTOOLCHAIN=local CGO_ENABLED=0 GOEXPERIMENT=simd\n"
    assert env in stage("build") and env in TOOLS
    # A plain go build (no experiment) is tested too: the same vectors, slower.
    run_sh = (REPO / "run.sh").read_text(encoding="utf-8")
    assert "GOEXPERIMENT= go test ./internal/encoder" in run_sh


def test_forgejo_runs_no_forks_code():
    """Forgejo's jobs get the host engine's socket (root on the host, and the
    registry token's next image job): a pull request from another repository
    runs no job there. The checks say so; every other job needs them or runs
    for no pull request (docs/spec/security.md, CI runs no stranger's code).
    A fork can change this file in its pull request: Forgejo's approval of a
    fork's run, which no test can check, holds then."""
    ci = (REPO / ".forgejo" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    jobs = re.split(r"\n  (?=[a-z]+:\n)", "\n" + ci.split("\njobs:\n", 1)[1])[1:]
    assert [j.split(":", 1)[0] for j in jobs] == ["checks", "bench", "retrieval", "image", "github"]
    for job in jobs:
        name = job.split(":", 1)[0]
        if name == "checks":
            assert "\n    if: github.event_name != 'pull_request' || " \
                   "github.event.pull_request.head.repo.full_name == github.repository\n" in job
        else:
            assert "needs: [checks]" in job, name


def test_only_forgejo_runs_the_bench():
    # The bench measures time against budgets: on Forgejo's runner, the same
    # machine every run. GitHub gets only what passed there, and its shared
    # runners would fail it for their neighbours, on the public main.
    assert "\n  bench:\n" in (REPO / ".forgejo" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    github = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "\n  bench:\n" not in github and "./run.sh bench" not in github


def test_a_tag_releases_the_binaries():
    # On a v* tag, once the checks pass, GitHub's CI builds the binaries with
    # run.sh (in the tools image, whose Go is the image's: above) and attaches
    # them, their sums and the licenses to the tag's release with gh.
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("\n  release:\n"):]
    for line in ("    needs: [checks]\n", "    if: startsWith(github.ref, 'refs/tags/v')\n", "      contents: write\n",
                 "        run: ./run.sh binaries\n", 'gh release upload "$TAG" dist/* --clobber\n'):
        assert line in job, line
    run_sh = (REPO / "run.sh").read_text(encoding="utf-8")
    # The tag is the version the binary says: the checks, which the image,
    # the binaries and Forgejo's push to GitHub wait for, fail a v* tag that
    # isn't (run.sh test), and the version is where that looks for it.
    tests = run_sh[run_sh.index("\n  test)\n"):run_sh.index("\n  bench)\n")]
    assert 'grep -qxF "const version = \\"${GITHUB_REF#refs/tags/v}\\"" go/config.go' in tests
    assert tests.index("refs/tags/v*)") < tests.index("build_image")
    # Its message, the release's notes, mentions no one (tests/test_tag_message.py),
    # read from the tag as it is, since actions/checkout may leave it lightweight.
    fetch = tests.index('git fetch -q --no-tags origin "+$GITHUB_REF:$GITHUB_REF"')
    assert fetch < tests.index('scripts/check-tag-message.sh "${GITHUB_REF#refs/tags/}"') < tests.index("build_image")
    assert re.search(r'^const version = "\d+\.\d+\.\d+(-dev)?"$', (REPO / "go" / "config.go").read_text(encoding="utf-8"), re.M)
    for workflow in (ci, (REPO / ".forgejo" / "workflows" / "ci.yml").read_text(encoding="utf-8")):
        assert "check: [test, e2e, leaks]" in workflow
    binaries = run_sh[run_sh.index("\n  binaries)\n"):]
    binaries = binaries[:binaries.index("\n    ;;\n")]
    assert "build_image zonewiki-tools -f Containerfile.tools" in binaries
    assert "for target in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64; do" in binaries
    assert 'CGO_ENABLED=0 GOOS=$os GOARCH=$arch go build -trimpath -ldflags="-s -w"' in binaries
    assert "cp LICENSE THIRD_PARTY_LICENSES dist/" in binaries
    assert "sha256sum zonewiki-* LICENSE THIRD_PARTY_LICENSES > SHA256SUMS" in binaries
    assert "/dist/" in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_the_version_is_not_older_than_the_latest_tag():
    # Between releases the version names the next one with "-dev" (Version in
    # docs/spec/deployment.md), so a build after a tag never says it is the tag.
    # Skipped where the checkout has no tags (a CI clone, an export).
    config = (REPO / "go" / "config.go").read_text(encoding="utf-8")
    version = re.search(r'^const version = "(\d+)\.(\d+)\.(\d+)(-dev)?"$', config, re.M)
    assert version, "const version is major.minor.patch, with -dev between releases"
    tags = subprocess.run(["git", "-C", str(REPO), "tag", "--list", "v[0-9]*"], capture_output=True, text=True)
    latest = [tuple(map(int, t[1:].split("."))) for t in tags.stdout.split() if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    if not latest:
        pytest.skip("no release tags in this checkout")
    now = tuple(map(int, version.groups()[:3]))
    newest = max(latest)
    # The tagged commit has the plain number; every later one is above it, with -dev.
    assert now > newest or now == newest and not version.group(4), f"version {now} is not above the latest tag {newest}"


def test_forgejos_runs_go_one_at_a_time():
    """One concurrency group for the repository, queued and not cancelled:
    two runs at once on the runner's host (main and a tag pushed together)
    failed the browser tests and the bench. On the workflow alone: Forgejo
    ignores a group on a job, which would only look as if it held."""
    ci = (REPO / ".forgejo" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "\nconcurrency:\n  group: ci\n  cancel-in-progress: false\n" in ci
    assert ci.count("concurrency:") == 1


def test_forgejo_publishes_to_the_other_repository_only_as_it_is():
    """The github job pushes main's commit or a tag to the other repository
    after the checks, never forced (so the hashes there are Forgejo's), only
    for a push, with GitHub's host key held to its published fingerprint and
    nothing sent without the key (docs/spec/deployment.md, Publishing to GitHub)."""
    ci = (REPO / ".forgejo" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("\n  github:\n"):]
    for line in ("    needs: [checks]\n", "    if: github.event_name == 'push' && vars.PUBLISH_REPO != ''\n",
                 "          fetch-depth: 0\n", "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU",
                 'if [ -z "$PUBLISH_KEY" ]; then', 'git push publish "$sha:refs/heads/main"',
                 'git push publish "$GITHUB_REF"', "-o BatchMode=yes"):
        assert line in job, line
    # Never forced: no push with --force, -f or a "+" refspec. (The tag's
    # fetch is forced on purpose: it replaces the lightweight tag that
    # actions/checkout leaves in the runner's clone with the annotated one.)
    pushes = [l for l in job.splitlines() if "git push" in l]
    assert len(pushes) == 2
    assert not any("--force" in l or " -f" in l or ' "+' in l for l in pushes), pushes
    assert job.count("--force") == 1 and 'git fetch -q --force origin "+$GITHUB_REF:$GITHUB_REF"' in job
    assert "StrictHostKeyChecking" not in job
    # The key and the check come from secrets, the repository from a variable.
    for name in ("PUBLISH_KEY", "PUBLISH_CHECK"):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in job
    assert "PUBLISH_REPO: ${{ vars.PUBLISH_REPO }}" in job


def test_the_target_architecture_has_no_default():
    # A default in the ARG wins over the one buildx sets, so the arm64 image
    # was built with amd64's Go ("Exec format error"). The Containerfile
    # falls back on the container's own architecture instead.
    assert not re.search(r"^ARG TARGETARCH=", CONTAINERFILE, re.M)
    # (Go alone: the model, the other download, is the same for every one.)
    assert CONTAINERFILE.count("${TARGETARCH:-$(dpkg --print-architecture)}") == 1
    # The tools image likewise (gitleaks and Go): on an arm64 machine
    # ./run.sh test got amd64's.
    assert not re.search(r"^ARG TARGETARCH=", TOOLS, re.M)
    assert TOOLS.count("${TARGETARCH:-$(dpkg --print-architecture)}") == 2
