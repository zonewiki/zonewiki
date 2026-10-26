# Leaf's image: the server (one Go binary), the browser code it serves, and
# with LEAF_SEMANTIC_SEARCH=on the model and onnxruntime that run semantic
# search. Three stages, so a change to the code rebuilds neither the model
# layer nor the toolchain's.

# ── Build: the server from go/ ───────────────────────────────────────────────
# With cgo (a C compiler): semantic search loads onnxruntime with dlopen, so
# the binary links only libc either way.
FROM ubuntu:26.04 AS build
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl gcc libc6-dev && \
    rm -rf /var/lib/apt/lists/*

# Go, a pinned release checked against its published checksum (go.dev/dl),
# the same as Containerfile.tools'. To update it, change the version and
# both sums in both files.
ARG TARGETARCH=amd64
ARG GO_VERSION=1.27.3
ARG GO_SHA256_AMD64=65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40
ARG GO_SHA256_ARM64=a06e99309dbcbdca1a8ccd2282c3ee146e55930886e26b4f1193269587165f49
RUN case "$TARGETARCH" in \
      amd64) sum=$GO_SHA256_AMD64 ;; \
      arm64) sum=$GO_SHA256_ARM64 ;; \
      *) echo "no Go build for $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl -sSfL -o /tmp/go.tgz "https://go.dev/dl/go${GO_VERSION}.linux-${TARGETARCH}.tar.gz" \
 && echo "$sum  /tmp/go.tgz" | sha256sum -c - \
 && tar -xzf /tmp/go.tgz -C /usr/local \
 && rm /tmp/go.tgz
ENV PATH=/usr/local/go/bin:$PATH GOTOOLCHAIN=local CGO_ENABLED=1

WORKDIR /src
# The modules first (go.sum checks them), so a code change doesn't fetch them again.
COPY go/go.mod go/go.sum ./
RUN go mod download
COPY go/ ./
RUN go build -trimpath -ldflags="-s -w" -o /leaf .

# ── Semantic search: onnxruntime and the model (opt-in) ─────────────────────
# With LEAF_SEMANTIC_SEARCH=on in .env, compose.yaml passes it here and both
# are baked into the image, so the first search doesn't wait for a download
# and Leaf works offline. Without it, the folders stay empty. Pinned and
# checked against their SHA-256: onnxruntime's GitHub release (the sums of
# its files), the model's files at a fixed revision (go/embed.go has the
# same names and sums; tests/test_image.py checks they agree).
FROM ubuntu:26.04 AS semantic
ARG TARGETARCH=amd64
ARG LEAF_SEMANTIC_SEARCH=
ARG ORT_VERSION=1.31.0
ARG ORT_SHA256_X64=cc5c72baf5ae5c8238a6841f0897227be2d02826b9cf98eaf02fdefaeeb03a57
ARG ORT_SHA256_AARCH64=c5b8b3cca31f3d643a3b313b8f42e2d0f16a4fcefd02cda343d97bd5306afaab
ARG MODEL=jinaai/jina-embeddings-v2-base-de
ARG MODEL_REVISION=3f9eede875721714945b6a99a3198299243cf2be
ARG MODEL_ONNX_SHA256=51654b7441fbfcfef6598b01cbd1ea925ca0f0cad81202fcd36fee325783f1b0
ARG MODEL_VOCAB_SHA256=376c53a06d6911569afc19f584002a11dbcd6b871afb6d96b8600eb3c4e7e7a7
RUN mkdir -p /opt/onnxruntime /opt/leaf-model \
 && case "$(echo "$LEAF_SEMANTIC_SEARCH" | tr '[:upper:]' '[:lower:]' | tr -d ' ')" in \
      ""|0|off|false|no) exit 0 ;; \
      1|on|true|yes) ;; \
      *) echo "LEAF_SEMANTIC_SEARCH: expected on or off, got '$LEAF_SEMANTIC_SEARCH'" >&2; exit 1 ;; \
    esac \
 && case "$TARGETARCH" in \
      amd64) arch=x64 sum=$ORT_SHA256_X64 ;; \
      arm64) arch=aarch64 sum=$ORT_SHA256_AARCH64 ;; \
      *) echo "no onnxruntime build for $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl \
 && curl -sSfL -o /tmp/ort.tgz \
      "https://github.com/microsoft/onnxruntime/releases/download/v${ORT_VERSION}/onnxruntime-linux-${arch}-${ORT_VERSION}.tgz" \
 && echo "$sum  /tmp/ort.tgz" | sha256sum -c - \
 && tar -xzf /tmp/ort.tgz -C /opt/onnxruntime --strip-components=2 --wildcards "*/lib/libonnxruntime.so*" \
 && base="https://huggingface.co/${MODEL}/resolve/${MODEL_REVISION}" \
 && mkdir -p /opt/leaf-model/onnx \
 && curl -sSfL -o /opt/leaf-model/onnx/model.onnx "$base/onnx/model.onnx" \
 && curl -sSfL -o /opt/leaf-model/tokenizer.json "$base/tokenizer.json" \
 && printf '%s  %s\n' "$MODEL_ONNX_SHA256" /opt/leaf-model/onnx/model.onnx \
      "$MODEL_VOCAB_SHA256" /opt/leaf-model/tokenizer.json | sha256sum -c - \
 && chmod -R a+rX /opt/leaf-model \
 && rm -rf /tmp/ort.tgz /var/lib/apt/lists/*

# ── The image ────────────────────────────────────────────────────────────────
FROM ubuntu:26.04

# git: the wiki's history. tzdata: TZ (compose.yaml, from the host or .env)
# names the user's timezone. ca-certificates: LEAF_GIT_REMOTE over https,
# and fetching the model when it isn't baked in.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      git tzdata ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# onnxruntime where the loader finds it, and the model where Leaf looks.
COPY --from=semantic /opt/onnxruntime/ /usr/local/lib/
COPY --from=semantic /opt/leaf-model/ /opt/leaf-model/
RUN ldconfig
ENV LEAF_MODEL_DIR=/opt/leaf-model

WORKDIR /app
COPY public/ public/
# Leaf's documentation, for the MCP tool leaf_help (found next to public/).
COPY README.md ./
COPY docs/spec/ docs/spec/
COPY --from=build /leaf ./leaf

# The build ("<hash> <commit date>") shown next to Leaf's version, passed in by
# run.sh (through compose.yaml): the image has no .git to read it from. Last, so a new commit only rebuilds this layer.
ARG LEAF_BUILD=unknown
ENV LEAF_BUILD=$LEAF_BUILD

# compose.yaml runs Leaf as the host user's UID (any UID, maybe without an
# account here): git gets a HOME it can use.
ENV HOME=/tmp

EXPOSE 5000

CMD ["/app/leaf"]
