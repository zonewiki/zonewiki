"""Which embedding model Leaf uses, and installing it ahead of time.

Kept apart from embed.py on purpose: the Containerfile copies only this file
and requirements-embed.txt before installing the packages and downloading the
model (0.64 GB), so that layer is rebuilt only when they or
LEAF_SEMANTIC_SEARCH change, not on every edit to embed.py. Keep it small and
stable.

    LEAF_SEMANTIC_SEARCH=on python3 embed_setup.py
"""

import hashlib
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

# German and English, also mixed (0.64 GB). On a test wiki it separated real
# hits from noise far better than the small multilingual MiniLM (which covers
# ~50 languages and is a third the size) or the English-only bge-small.
# The only model: embed.py runs it itself (onnxruntime and tokenizers, no
# fastembed), with this model's recipe (mean pooling, no prefixes), which
# another model may not share.
DEFAULT_MODEL = "jinaai/jina-embeddings-v2-base-de"
# The model's files at a fixed revision, each checked against its SHA-256.
# (gitleaks takes a hash next to "token…" for an API key: allowed by line.)
MODEL_REVISION = "3f9eede875721714945b6a99a3198299243cf2be"
MODEL_FILES = {
    "onnx/model.onnx": "51654b7441fbfcfef6598b01cbd1ea925ca0f0cad81202fcd36fee325783f1b0",
    "tokenizer.json": "376c53a06d6911569afc19f584002a11dbcd6b871afb6d96b8600eb3c4e7e7a7",  # gitleaks:allow
}


# The settings' names until 2026-10 (LEAF_EMBEDDINGS=fastembed): set, they
# turn nothing on, and embed.from_env logs what to rename them to.
RENAMED = {"LEAF_EMBEDDINGS": "LEAF_SEMANTIC_SEARCH", "LEAF_EMBED_MIN_SCORE": "LEAF_SEMANTIC_MIN_SCORE"}


def parse_spec(value: str) -> str | None:
    """The model LEAF_SEMANTIC_SEARCH turns on: DEFAULT_MODEL for on (1,
    true, yes), None for off (0, false, no, empty); anything else is an
    error."""
    v = (value or "").strip().lower()
    if v in ("", "0", "off", "false", "no"):
        return None
    if v in ("1", "on", "true", "yes"):
        return DEFAULT_MODEL
    raise ValueError(f"LEAF_SEMANTIC_SEARCH: expected on or off, got {value!r}")


def model_dir() -> Path:
    """Where the model's files are: LEAF_MODEL_DIR (the image's /opt/leaf-model),
    else a cache folder in the home directory, per revision."""
    if os.environ.get("LEAF_MODEL_DIR"):
        return Path(os.environ["LEAF_MODEL_DIR"])
    return Path.home() / ".cache" / "leaf" / f"{DEFAULT_MODEL.replace('/', '--')}@{MODEL_REVISION[:12]}"


def download_model(folder: Path) -> None:
    """Fetch the files MODEL_FILES names that folder doesn't have yet, each
    checked against its hash before it takes its name (a half download or a
    changed file never counts as the model)."""
    for name, sha in MODEL_FILES.items():
        dest = folder / name
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        url = f"https://huggingface.co/{DEFAULT_MODEL}/resolve/{MODEL_REVISION}/{name}"
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
            while chunk := resp.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != sha:
            tmp.unlink()
            raise RuntimeError(f"{url}: SHA-256 {digest.hexdigest()}, expected {sha}")
        tmp.replace(dest)


def prepare() -> None:
    """Install the packages requirements-embed.txt pins (onnxruntime, the
    tokenizer, NumPy, which embed.py also scores with), download the model
    into model_dir() and load it once, so a broken install fails the build
    (build time). Nothing when off. (embed.py isn't in the image yet here.)"""
    if not parse_spec(os.environ.get("LEAF_SEMANTIC_SEARCH", "")):
        return
    # --no-deps: the file lists every package; tokenizers would otherwise
    # bring the Hugging Face hub client, which only downloads.
    subprocess.run([sys.executable, "-m", "pip", "install", "--break-system-packages",
                    "--no-cache-dir", "--no-deps", "-q", "-r",
                    str(Path(__file__).with_name("requirements-embed.txt"))], check=True)
    folder = model_dir()
    download_model(folder)
    import onnxruntime
    from tokenizers import Tokenizer
    Tokenizer.from_file(str(folder / "tokenizer.json"))
    onnxruntime.InferenceSession(str(folder / "onnx/model.onnx"), providers=["CPUExecutionProvider"])
    print(f"Downloaded {DEFAULT_MODEL} to {folder}")


if __name__ == "__main__":
    prepare()
