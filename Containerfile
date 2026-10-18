FROM ubuntu:26.04

# tzdata: TZ (compose.yaml, from the host or .env) names the user's timezone.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      python3 python3-pip git tzdata && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src/requirements.txt .
RUN pip install --break-system-packages -r requirements.txt

# Semantic search (opt-in): with LEAF_SEMANTIC_SEARCH=on in .env, compose.yaml
# passes it here and onnxruntime, the tokenizer and the model are baked into the image, so the first
# search doesn't wait for a download and Leaf works offline. Without it,
# this step does nothing. Only the small, stable
# embed_setup.py and the versions it installs (requirements-embed.txt) are
# copied first, so this layer (0.64 GB) is rebuilt when the model setting or
# those versions change, not on every change to the search code. The model
# folder is made world-readable: the container may run as another user.
ARG LEAF_SEMANTIC_SEARCH=
ENV LEAF_MODEL_DIR=/opt/leaf-model
COPY src/embed_setup.py src/requirements-embed.txt ./
RUN python3 embed_setup.py && \
    if [ -d /opt/leaf-model ]; then chmod -R a+rX /opt/leaf-model; fi

COPY src/ .

# Leaf's documentation, for the MCP tool leaf_help (manual.py looks for it
# next to the code).
COPY README.md ./
COPY docs/spec/ docs/spec/

# The build ("<hash> <commit date>") shown next to Leaf's version, passed in by
# run.sh (through compose.yaml): the image has no .git to read it from. Last, so a new commit only rebuilds this layer.
ARG LEAF_BUILD=unknown
ENV LEAF_BUILD=$LEAF_BUILD

# compose.yaml runs Leaf as the host user's UID (any UID, maybe without an
# account here): git and Python get a HOME they can use.
ENV HOME=/tmp

EXPOSE 5000

CMD ["python3", "server.py"]
