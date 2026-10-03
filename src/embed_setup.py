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


def parse_spec(spec: str) -> tuple[str, str] | None:
    """LEAF_EMBEDDINGS as (kind, model): `fastembed`, `fastembed:<model>` or
    `ollama:<model>`; None when it is empty or off."""
    spec = (spec or "").strip()
    if not spec or spec.lower() in ("0", "off", "false", "no"):
        return None
    kind, _, model = spec.partition(":")
    kind, model = kind.lower(), model.strip()
    if kind in ("fastembed", "1", "true", "yes", "on"):
        return "fastembed", model or DEFAULT_FASTEMBED_MODEL
    if kind == "ollama" and model:
        return "ollama", model
    raise ValueError(f"LEAF_EMBEDDINGS: expected fastembed[:model] or ollama:<model>, got {spec!r}")


def prepare() -> None:
    """Install fastembed and download LEAF_EMBEDDINGS' model into
    FASTEMBED_CACHE_PATH (build time). Nothing to do for Ollama or when off."""
    parsed = parse_spec(os.environ.get("LEAF_EMBEDDINGS", ""))
    if not parsed or parsed[0] != "fastembed":
        return
    subprocess.run([sys.executable, "-m", "pip", "install", "--break-system-packages",
                    "--no-cache-dir", "-q", "fastembed"], check=True)
    from fastembed import TextEmbedding
    model = TextEmbedding(parsed[1], cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
    list(model.query_embed(["warm up"]))
    print(f"Downloaded {parsed[1]}")


if __name__ == "__main__":
    prepare()
