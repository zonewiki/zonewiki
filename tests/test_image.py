"""The Containerfiles: pins that must agree with each other and with the
code, and the layering that keeps a code change from refetching the model."""

import hashlib
import re

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
    assert f'{{"onnx/model.onnx", "{a["MODEL_ONNX_SHA256"]}"}}' in embed
    assert f'{{"tokenizer.json", "{a["MODEL_VOCAB_SHA256"]}"}}' in embed


def test_the_semantic_layer_takes_nothing_from_the_repo():
    # Else every change to the code would download the model again.
    assert "COPY" not in stage("semantic")


def test_base_images_and_ci_actions_are_pinned_by_hash():
    # A tag can be moved to other content: what builds and publishes Zone
    # names the exact image (its digest) and the exact commit of an action.
    bases = dict(re.findall(r"^ARG (UBUNTU)=(\S+)", CONTAINERFILE, re.M))
    froms = re.findall(r"^FROM (\S+)", CONTAINERFILE + TOOLS, re.M)
    assert froms.count("${UBUNTU}") == 3 and len(froms) == 4
    for image in [bases["UBUNTU"]] + [f for f in froms if f != "${UBUNTU}"]:
        assert re.fullmatch(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}", image), image
    ci = "".join((REPO / d / "workflows" / "ci.yml").read_text(encoding="utf-8") for d in (".github", ".forgejo"))
    for image in re.findall(r"^\s+image: (\S+)$", ci, re.M):   # a job's container (Forgejo)
        assert re.fullmatch(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}", image), image
    uses = re.findall(r"uses: (\S+)(.*)", ci)
    assert len(uses) > 8
    for action, comment in uses:
        assert re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", action), action
        assert re.fullmatch(r" # v\d+\.\d+\.\d+", comment), action   # the release it is


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
    # go/go.mod takes the module of Zone's own files from ../ (replace): both
    # images fetch the modules with the root's go.mod beside go/.
    assert "require zonefiles v0.0.0\n\nreplace zonefiles => ../\n" in (REPO / "go" / "go.mod").read_text(encoding="utf-8")
    assert re.search(r"^module zonefiles$", (REPO / "go.mod").read_text(encoding="utf-8"), re.M)
    build = stage("build")
    assert build.index("COPY go.mod /src/") < build.index("RUN go mod download") < build.index("COPY go/ ./")
    assert "COPY go.mod /tmp/gomod/\nCOPY go/go.mod go/go.sum /tmp/gomod/go/\nRUN cd /tmp/gomod/go && go mod download" in TOOLS


def test_the_image_ships_the_licenses():
    final = CONTAINERFILE[CONTAINERFILE.rindex("\nFROM "):]
    assert "COPY LICENSE THIRD_PARTY_LICENSES ./" in final
    assert "COPY --from=semantic /opt/onnxruntime-licenses/ licenses/onnxruntime/" in final
    assert '-C /opt/onnxruntime-licenses --strip-components=1 --wildcards "*/LICENSE" "*/ThirdPartyNotices.txt"' in stage("semantic")


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
    # Zone ship it under the AGPL, and both licenses ask for a way to get the source.
    assert "License: EPL-2.0 OR GPL-3.0-or-later" in notices
    assert "https://github.com/kieler/elkjs" in notices
    assert "GNU GENERAL PUBLIC LICENSE" in (folder / "COPYING.GPL-3.0").read_text(encoding="utf-8")


def test_the_binary_is_zonewiki():
    final = CONTAINERFILE[CONTAINERFILE.rindex("\nFROM "):]
    assert "COPY --from=build /zonewiki ./zonewiki" in final
    # Every interface in the container, for its network bridge: the binary's
    # default port alone would be the container's localhost, out of reach.
    assert 'CMD ["/app/zonewiki", "--addr", "0.0.0.0:5000"]' in final
    for compose in ("compose.yaml", "compose.nas.yaml"):
        assert '"/app/zonewiki", "health"' in (REPO / compose).read_text(encoding="utf-8")


def test_ci_publishes_no_onnxruntime_while_it_is_paused():
    # Semantic search paused (docs/spec/deployment.md): since 2026-11-18,
    # Microsoft has onnxruntime's repository private "while we investigate
    # malicious content". Neither CI builds an image with it or runs the
    # retrieval job, which runs it; lift both together, on purpose.
    for path in (".github/workflows/ci.yml", ".forgejo/workflows/ci.yml"):
        ci = (REPO / path).read_text(encoding="utf-8")
        assert "ZONE_SEMANTIC_SEARCH=off" in ci and "ZONE_SEMANTIC_SEARCH=on" not in ci, path
        job = ci[ci.index("\n  retrieval:\n"):]
        job = job[:job.index("\n  image:\n")]
        assert "\n    if: false\n" in job, path


def test_forgejo_runs_no_forks_code():
    """Forgejo's jobs get the host engine's socket (root on the host, and the
    registry token's next image job): a pull request from another repository
    runs no job there. The checks say so; every other job needs them or runs
    for no pull request (docs/spec/security.md, CI runs no stranger's code)."""
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
    binaries = run_sh[run_sh.index("\n  binaries)\n"):]
    binaries = binaries[:binaries.index("\n    ;;\n")]
    assert "build_image zonewiki-tools -f Containerfile.tools" in binaries
    assert "for target in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64; do" in binaries
    assert 'CGO_ENABLED=0 GOOS=$os GOARCH=$arch go build -trimpath -ldflags="-s -w"' in binaries
    assert "cp LICENSE THIRD_PARTY_LICENSES dist/" in binaries
    assert "sha256sum zonewiki-* LICENSE THIRD_PARTY_LICENSES > SHA256SUMS" in binaries
    assert "/dist/" in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()


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
    assert "--force" not in job and "git push -f" not in job and ' "+' not in job
    assert "StrictHostKeyChecking" not in job
    # The key and the check come from secrets, the repository from a variable.
    for name in ("PUBLISH_KEY", "PUBLISH_CHECK"):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in job
    assert "PUBLISH_REPO: ${{ vars.PUBLISH_REPO }}" in job
