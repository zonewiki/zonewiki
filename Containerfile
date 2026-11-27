# Zonewiki's image: the server (one Go binary, with the browser code and the docs
# it serves built in), and with ZONE_SEMANTIC_SEARCH=on the model of semantic
# search, which the binary runs itself. Three stages, so a change to the code
# rebuilds neither the model layer nor the toolchain's.

# The base image, by digest (the tag's multi-arch index): a tag can be moved
# to other content, a digest can't. It doesn't follow the tag's updates, so
# bump it now and then, here and nowhere else:
#   podman manifest inspect docker://ubuntu:26.04 >/dev/null; skopeo inspect docker://ubuntu:26.04 | grep Digest
# or the Docker-Content-Digest header of the registry's manifest for the tag.
ARG UBUNTU=ubuntu:26.04@sha256:065aa8626841f16057b78b027b464cd59c4fac36c204c72dcbf48d66f5fa8a96

# ── Build: the server from go/ ───────────────────────────────────────────────
# Without cgo: the binary is static and needs no C compiler. Semantic search's
# model runs in Go on vector arithmetic (go/internal/encoder), which is Go's
# simd package: an experiment, so GOEXPERIMENT=simd is set for the build
# (without it the binary still builds, and embeds many times slower).
# Zonewiki's own files (zonefiles.go at the repository's root: the browser
# code, the docs, the licenses) are built in.
FROM ${UBUNTU} AS build
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

# Go, a pinned release checked against its published checksum (go.dev/dl),
# the same as Containerfile.tools'. To update it, change the version and
# both sums in both files.
# TARGETARCH is set by buildx and podman for the platform being built; it has
# no default here, since a default wins over it (the arm64 build got amd64's
# Go). Where the builder doesn't set it, the container's own architecture is it.
ARG TARGETARCH
ARG GO_VERSION=1.27.3
ARG GO_SHA256_AMD64=65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40
ARG GO_SHA256_ARM64=a06e99309dbcbdca1a8ccd2282c3ee146e55930886e26b4f1193269587165f49
RUN TARGETARCH=${TARGETARCH:-$(dpkg --print-architecture)} \
 && case "$TARGETARCH" in \
      amd64) sum=$GO_SHA256_AMD64 ;; \
      arm64) sum=$GO_SHA256_ARM64 ;; \
      *) echo "no Go build for $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl -sSfL -o /tmp/go.tgz "https://go.dev/dl/go${GO_VERSION}.linux-${TARGETARCH}.tar.gz" \
 && echo "$sum  /tmp/go.tgz" | sha256sum -c - \
 && tar -xzf /tmp/go.tgz -C /usr/local \
 && rm /tmp/go.tgz
ENV PATH=/usr/local/go/bin:$PATH GOTOOLCHAIN=local CGO_ENABLED=0 GOEXPERIMENT=simd

WORKDIR /src/go
# The modules first (go.sum checks them), so a code change doesn't fetch them
# again; with the root's go.mod, the module of Zonewiki's own files, which
# go/go.mod takes from ../.
COPY go.mod /src/
COPY go/go.mod go/go.sum ./
RUN go mod download
# What zonefiles.go builds into the binary (its go:embed line).
COPY zonefiles.go README.md LICENSE THIRD_PARTY_LICENSES /src/
COPY public/ /src/public/
COPY docs/manual/ /src/docs/manual/
COPY docs/spec/ /src/docs/spec/
COPY docs/screenshots/*.png /src/docs/screenshots/
COPY go/ ./
RUN go build -trimpath -ldflags="-s -w" -o /zonewiki .

# ── Semantic search: the model (opt-in) ──────────────────────────────────────
# With ZONE_SEMANTIC_SEARCH=on in .env, compose.build.yaml passes it here and
# the model is baked into the image, so the first search doesn't wait for a
# download and Zonewiki works offline. Without it, the folder stays empty.
# Pinned and checked against their SHA-256: the model's files at a fixed
# revision (go/embed.go has the same names and sums; tests/test_image.py
# checks they agree). Nothing runs it but the binary: no runtime is fetched.
FROM ${UBUNTU} AS semantic
ARG ZONE_SEMANTIC_SEARCH=
ARG MODEL=jinaai/jina-embeddings-v2-base-de
ARG MODEL_REVISION=3f9eede875721714945b6a99a3198299243cf2be
ARG MODEL_ONNX_SHA256=51654b7441fbfcfef6598b01cbd1ea925ca0f0cad81202fcd36fee325783f1b0
ARG MODEL_VOCAB_SHA256=376c53a06d6911569afc19f584002a11dbcd6b871afb6d96b8600eb3c4e7e7a7
RUN mkdir -p /opt/zonewiki-model \
 && case "$(echo "$ZONE_SEMANTIC_SEARCH" | tr '[:upper:]' '[:lower:]' | tr -d ' ')" in \
      ""|0|off|false|no) exit 0 ;; \
      1|on|true|yes) ;; \
      *) echo "ZONE_SEMANTIC_SEARCH: expected on or off, got '$ZONE_SEMANTIC_SEARCH'" >&2; exit 1 ;; \
    esac \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl \
 && base="https://huggingface.co/${MODEL}/resolve/${MODEL_REVISION}" \
 && mkdir -p /opt/zonewiki-model/onnx \
 && curl -sSfL -o /opt/zonewiki-model/onnx/model.onnx "$base/onnx/model.onnx" \
 && curl -sSfL -o /opt/zonewiki-model/tokenizer.json "$base/tokenizer.json" \
 && printf '%s  %s\n' "$MODEL_ONNX_SHA256" /opt/zonewiki-model/onnx/model.onnx \
      "$MODEL_VOCAB_SHA256" /opt/zonewiki-model/tokenizer.json | sha256sum -c - \
 && chmod -R a+rX /opt/zonewiki-model \
 && rm -rf /var/lib/apt/lists/*

# ── The image ────────────────────────────────────────────────────────────────
FROM ${UBUNTU}

# git: the wiki's history. tzdata: TZ (compose.build.yaml, from the host or .env)
# names the user's timezone. ca-certificates: a git-remote over https,
# and fetching the model when it isn't baked in.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      git tzdata ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# The model where Zonewiki looks.
COPY --from=semantic /opt/zonewiki-model/ /opt/zonewiki-model/
ENV ZONE_MODEL_DIR=/opt/zonewiki-model

WORKDIR /app
# The browser code and the docs (Help, zone_help) are the binary's own copies:
# no public/ beside it. The licenses of what the image holds, to read
# (zonewiki licenses prints them, the vendored libraries' too).
COPY LICENSE THIRD_PARTY_LICENSES ./
COPY --from=build /zonewiki ./zonewiki

# The build ("<hash> <commit date>") shown next to Zonewiki's version, passed in by
# run.sh (through compose.build.yaml): the image has no .git to read it from. Last, so a new commit only rebuilds this layer.
ARG ZONE_BUILD=unknown
ENV ZONE_BUILD=$ZONE_BUILD

# The compose files run Zonewiki as the host user's UID (any UID, maybe without
# an account here): git gets a HOME it can use.
ENV HOME=/tmp

EXPOSE 5080

CMD ["/app/zonewiki", "--addr", "0.0.0.0:5080"]
