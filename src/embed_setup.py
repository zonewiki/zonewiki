"""Which embedding model Leaf uses, and installing it ahead of time.

Kept apart from embed.py on purpose: the Containerfile copies only this file
before installing fastembed and downloading the model (0.64 GB), so that
layer is rebuilt only when this file or LEAF_EMBEDDINGS changes, not on
every edit to embed.py. Keep it small and stable.

    LEAF_EMBEDDINGS=fastembed python3 embed_setup.py
"""

import os
import subprocess
import sys

# German and English, also mixed (0.64 GB). On a test wiki it separated real
# hits from noise far better than the small multilingual MiniLM (which covers
# ~50 languages and is a third the size) or the English-only bge-small.
DEFAULT_FASTEMBED_MODEL = "jinaai/jina-embeddings-v2-base-de"


def parse_spec(spec: str) -> str | None:
    """The fastembed model LEAF_EMBEDDINGS names: `fastembed` (the default
    model) or `fastembed:<model>`; None when it is empty or off."""
    spec = (spec or "").strip()
    if not spec or spec.lower() in ("0", "off", "false", "no"):
        return None
    kind, _, model = spec.partition(":")
    if kind.lower() in ("fastembed", "1", "true", "yes", "on"):
        return model.strip() or DEFAULT_FASTEMBED_MODEL
    raise ValueError(f"LEAF_EMBEDDINGS: expected fastembed or fastembed:<model>, got {spec!r}")


def prepare() -> None:
    """Install fastembed (it brings NumPy, which embed.py scores with) and
    download LEAF_EMBEDDINGS' model into FASTEMBED_CACHE_PATH (build time).
    Nothing when off."""
    name = parse_spec(os.environ.get("LEAF_EMBEDDINGS", ""))
    if not name:
        return
    subprocess.run([sys.executable, "-m", "pip", "install", "--break-system-packages",
                    "--no-cache-dir", "-q", "fastembed"], check=True)
    from fastembed import TextEmbedding
    model = TextEmbedding(name, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
    list(model.query_embed(["warm up"]))
    print(f"Downloaded {name}")


if __name__ == "__main__":
    prepare()
