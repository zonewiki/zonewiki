# Zone's image: the server (one Go binary), the browser code it serves, and
# with ZONE_SEMANTIC_SEARCH=on the model and onnxruntime that run semantic
# search. Three stages, so a change to the code rebuilds neither the model
# layer nor the toolchain's.

# The base image, by digest (the tag's multi-arch index): a tag can be moved
# to other content, a digest can't. It doesn't follow the tag's updates, so
# bump it now and then, here and nowhere else:
#   podman manifest inspect docker://ubuntu:26.04 >/dev/null; skopeo inspect docker://ubuntu:26.04 | grep Digest
# or the Docker-Content-Digest header of the registry's manifest for the tag.
ARG UBUNTU=ubuntu:26.04@sha256:065aa8626841f16057b78b027b464cd59c4fac36c204c72dcbf48d66f5fa8a96

# ── Build: the server from go/ ───────────────────────────────────────────────
# With cgo (a C compiler): semantic search loads onnxruntime with dlopen, so
# the binary links only libc either way.
FROM ${UBUNTU} AS build
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
RUN go build -trimpath -ldflags="-s -w" -o /zonewiki .

# ── Semantic search: onnxruntime and the model (opt-in) ─────────────────────
# With ZONE_SEMANTIC_SEARCH=on in .env, compose.yaml passes it here and both
# are baked into the image, so the first search doesn't wait for a download
# and Zone works offline. Without it, the folders stay empty. Pinned and
# checked against their SHA-256: onnxruntime's GitHub release (the sums of
# its files), the model's files at a fixed revision (go/embed.go has the
# same names and sums; tests/test_image.py checks they agree).
FROM ${UBUNTU} AS semantic
ARG TARGETARCH=amd64
ARG ZONE_SEMANTIC_SEARCH=
ARG ORT_VERSION=1.31.0
ARG ORT_SHA256_X64=cc5c72baf5ae5c8238a6841f0897227be2d02826b9cf98eaf02fdefaeeb03a57
ARG ORT_SHA256_AARCH64=c5b8b3cca31f3d643a3b313b8f42e2d0f16a4fcefd02cda343d97bd5306afaab
ARG MODEL=jinaai/jina-embeddings-v2-base-de
ARG MODEL_REVISION=3f9eede875721714945b6a99a3198299243cf2be
ARG MODEL_ONNX_SHA256=51654b7441fbfcfef6598b01cbd1ea925ca0f0cad81202fcd36fee325783f1b0
ARG MODEL_VOCAB_SHA256=376c53a06d6911569afc19f584002a11dbcd6b871afb6d96b8600eb3c4e7e7a7
RUN mkdir -p /opt/onnxruntime /opt/onnxruntime-licenses /opt/zonewiki-model \
 && case "$(echo "$ZONE_SEMANTIC_SEARCH" | tr '[:upper:]' '[:lower:]' | tr -d ' ')" in \
      ""|0|off|false|no) exit 0 ;; \
      1|on|true|yes) ;; \
      *) echo "ZONE_SEMANTIC_SEARCH: expected on or off, got '$ZONE_SEMANTIC_SEARCH'" >&2; exit 1 ;; \
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
 && tar -xzf /tmp/ort.tgz -C /opt/onnxruntime-licenses --strip-components=1 --wildcards "*/LICENSE" "*/ThirdPartyNotices.txt" \
 && base="https://huggingface.co/${MODEL}/resolve/${MODEL_REVISION}" \
 && mkdir -p /opt/zonewiki-model/onnx \
 && curl -sSfL -o /opt/zonewiki-model/onnx/model.onnx "$base/onnx/model.onnx" \
 && curl -sSfL -o /opt/zonewiki-model/tokenizer.json "$base/tokenizer.json" \
 && printf '%s  %s\n' "$MODEL_ONNX_SHA256" /opt/zonewiki-model/onnx/model.onnx \
      "$MODEL_VOCAB_SHA256" /opt/zonewiki-model/tokenizer.json | sha256sum -c - \
 && chmod -R a+rX /opt/zonewiki-model \
 && rm -rf /tmp/ort.tgz /var/lib/apt/lists/*

# ── The image ────────────────────────────────────────────────────────────────
FROM ${UBUNTU}

# git: the wiki's history. tzdata: TZ (compose.yaml, from the host or .env)
# names the user's timezone. ca-certificates: a git-remote over https,
# and fetching the model when it isn't baked in.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      git tzdata ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# onnxruntime where the loader finds it, and the model where Zone looks.
COPY --from=semantic /opt/onnxruntime/ /usr/local/lib/
COPY --from=semantic /opt/zonewiki-model/ /opt/zonewiki-model/
RUN ldconfig
ENV ZONE_MODEL_DIR=/opt/zonewiki-model

WORKDIR /app
COPY public/ public/
# Zone's documentation, for the MCP tool zone_help (found next to public/).
COPY README.md ./
COPY docs/spec/ docs/spec/
# The licenses of what the image holds (public/vendor/ has the rest).
COPY LICENSE THIRD_PARTY_LICENSES ./
COPY --from=semantic /opt/onnxruntime-licenses/ licenses/onnxruntime/
COPY --from=build /zonewiki ./zonewiki
# The binary was called leaf before the rename: compose files and healthchecks
# from then still call /app/leaf, for one release.
RUN ln -s zonewiki leaf

# The build ("<hash> <commit date>") shown next to Zone's version, passed in by
# run.sh (through compose.yaml): the image has no .git to read it from. Last, so a new commit only rebuilds this layer.
ARG ZONE_BUILD=unknown
ENV ZONE_BUILD=$ZONE_BUILD

# compose.yaml runs Zone as the host user's UID (any UID, maybe without an
# account here): git gets a HOME it can use.
ENV HOME=/tmp

EXPOSE 5000

CMD ["/app/zonewiki"]
