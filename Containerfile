FROM ubuntu:26.04

RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-pip git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src/requirements.txt .
RUN pip install --break-system-packages -r requirements.txt

# Semantic search (opt-in): with LEAF_EMBEDDINGS set in .env, run.sh passes
# it here and fastembed plus the model are baked into the image, so the first
# search doesn't wait for a download and Leaf works offline. Without it, or
# with an ollama: model, this step does nothing.
ARG LEAF_EMBEDDINGS=
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
COPY src/embed.py .
RUN python3 embed.py --prepare

COPY src/ .

# The build ("<hash> <commit date>") shown next to Leaf's version, passed in by
# run.sh: the image has no .git to read it from. Last, so a new commit only rebuilds this layer.
ARG LEAF_BUILD=unknown
ENV LEAF_BUILD=$LEAF_BUILD

EXPOSE 5000

CMD ["python3", "server.py"]
