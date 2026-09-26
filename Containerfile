FROM ubuntu:26.04

RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-pip git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src/requirements.txt .
RUN pip install --break-system-packages -r requirements.txt

COPY src/ .

# The build ("<hash> <commit date>") shown next to Leaf's version, passed in by
# run.sh: the image has no .git to read it from. Last, so a new commit only rebuilds this layer.
ARG LEAF_BUILD=unknown
ENV LEAF_BUILD=$LEAF_BUILD

EXPOSE 5000

CMD ["python3", "server.py"]
