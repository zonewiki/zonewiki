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
    assert f"go {args(TOOLS)['GO_VERSION']}" in (REPO / "go" / "go.mod").read_text(encoding="utf-8")


def test_the_image_ships_the_docs_and_the_browser_code():
    # zone_help finds README.md and docs/spec/ next to public/.
    final = CONTAINERFILE[CONTAINERFILE.rindex("\nFROM "):]
    for line in ("COPY public/ public/", "COPY README.md ./", "COPY docs/spec/ docs/spec/"):
        assert line in final


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
    assert 'CMD ["/app/zonewiki"]' in final
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
