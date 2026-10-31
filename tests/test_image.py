"""The Containerfiles: pins that must agree with each other and with the
code, and the layering that keeps a code change from refetching the model."""

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


def test_both_images_build_with_the_same_go():
    for key in ("GO_VERSION", "GO_SHA256_AMD64", "GO_SHA256_ARM64"):
        assert args(stage("build"))[key] == args(TOOLS)[key], key
    assert f"go {args(TOOLS)['GO_VERSION']}" in (REPO / "go" / "go.mod").read_text(encoding="utf-8")


def test_the_image_ships_the_docs_and_the_browser_code():
    # leaf_help finds README.md and docs/spec/ next to public/.
    final = CONTAINERFILE[CONTAINERFILE.rindex("\nFROM "):]
    for line in ("COPY public/ public/", "COPY README.md ./", "COPY docs/spec/ docs/spec/"):
        assert line in final


def test_the_binary_is_zonewiki_and_still_answers_as_leaf():
    # Compose files and healthchecks from before the rename call /app/leaf.
    final = CONTAINERFILE[CONTAINERFILE.rindex("\nFROM "):]
    assert "COPY --from=build /zonewiki ./zonewiki" in final
    assert "RUN ln -s zonewiki leaf" in final
    assert 'CMD ["/app/zonewiki"]' in final
    for compose in ("compose.yaml", "compose.nas.yaml"):
        assert '"/app/zonewiki", "health"' in (REPO / compose).read_text(encoding="utf-8")
