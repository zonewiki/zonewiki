# Semantic search prototype

Can a Go port of Leaf compute the same embeddings as `src/embed.py`
(`OnnxEmbedder`: jina-embeddings-v2-base-de on onnxruntime, Hugging Face's
tokenizer)? This is the embedding step in Go, with a harness that embeds a
wiki with both and compares token ids and vectors.

- `tokenizer.go`: the model's tokenizer, written for its `tokenizer.json`:
  byte-level BPE (GPT-2's), no normalizer, RoBERTa's `<s> … </s>`, at most
  512 tokens, special tokens in the text split out first (`<mask>` takes
  the whitespace before it). About 250 lines, no tokenizer library;
  `LoadTokenizer` refuses a `tokenizer.json` that wants more than this.
  go-huggingface (v0.4.16, the newest) still splits byte-level text at
  spaces only rather than with GPT-2's pattern, the bug the October spike
  had to patch; this one is GPT-2's pattern as a scanner (it has a lookahead
  RE2 lacks).
- `embed.go`: the model on onnxruntime through `github.com/yalue/onnxruntime_go`,
  batches padded with `<pad>`, the mean over the attention mask and the
  normalisation in float32 as numpy does them (a strided sum along the
  sequence, a pairwise sum of squares for the norm), then `embed._normalize`.

## Running it

Needs Go ≥ 1.27 with cgo (a C compiler), onnxruntime's shared library
(1.31.0, as `requirements-embed.txt` pins it for Python: the release's
`onnxruntime-linux-x64-1.31.0.tgz` or `-aarch64-`), the model (embed_setup
downloads it at its pinned revision) and a Python with the semantic packages.

```bash
cd prototype/semantic
python -m venv /tmp/venv-embed && /tmp/venv-embed/bin/pip install -r ../../src/requirements.txt \
  && /tmp/venv-embed/bin/pip install --no-deps -r ../../src/requirements-embed.txt packaging
(cd ../../src && /tmp/venv-embed/bin/python -c 'from pathlib import Path; from embed_setup import download_model; download_model(Path("/tmp/leaf-model"))')
LEAF_MODEL_DIR=/tmp/leaf-model go test ./...      # without it the tokenizer tests skip
go build -o /tmp/leafembed .
cp -a ~/leaf /tmp/wiki-copy
/tmp/venv-embed/bin/python compare.py /tmp/wiki-copy /tmp/leafembed /tmp/leaf-model /path/to/libonnxruntime.so
```

The texts are what the semantic index embeds: every page's sections as
`title\nsection` and every task's search text, in batches of 64 (the
index's worker); then queries one at a time (`embed_query`): the wiki's
`_eval/retrieval.json` questions and tokenizer edge cases (special tokens
in the text, contractions, whitespace runs, tabs and line breaks, emoji,
other scripts, combining marks, texts past 512 tokens, the empty text).

## Results (2026-10-26)

On a real wiki (2,508 sections, 312 tasks), 16 CPUs:

| | documents (2,820, batches of 64) | queries (76, one at a time) |
|---|---|---|
| token ids the same | 2,820 | 76 |
| vectors bit for bit the same | 2,820 | 76 |
| Python | 16.5 texts/s | 79.4 texts/s |
| Go | 16.7 texts/s | 79.7 texts/s |

The vectors are identical, not close: same ids, same batch shapes, so the
same onnxruntime kernels, and the same float32 steps after. A Go port can
keep Leaf's semantic cache (`.leaf/index/embeddings.json`, base64 float32)
as it is. The speed is onnxruntime's, the same either way.

## What it means for deployment

- The binary links only libc: `onnxruntime_go` loads `libonnxruntime.so`
  with `dlopen` when semantic search starts. So one binary does both: the
  keyword-only image doesn't ship the library, the semantic one adds it
  (29 MB) and the model, as the Python image adds its packages now.
- It needs cgo, so a C compiler at build time, and glibc at run time (not a
  static binary). Building for arm64 needs an arm64 build (native or under
  emulation, as CI builds the image now) or a cross compiler.
- The library's version and the binding's must suit each other; onnxruntime
  1.31.0 with onnxruntime_go 1.38.0 here.
