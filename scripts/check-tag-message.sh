#!/bin/sh
# Checks a release tag's message before it becomes the release's notes on
# GitHub (gh release create --notes-from-tag; Release binaries in
# docs/spec/deployment.md):
#
#   scripts/check-tag-message.sh v0.7.0    the tag in this repository
#   scripts/check-tag-message.sh -         a message on stdin
#
# The tag must be annotated: a lightweight one has no message, so the
# release would have no notes. And the message may not mention anyone: GitHub
# reads a bare @word in a release's notes as a mention of the account of that
# name, notifies it and lists it among the release's contributors (a note
# about the editor's @name once did). Write such a word as code (`@name`),
# where GitHub doesn't look; code blocks are skipped too, as are addresses
# (a@example.org). run.sh test runs this on a v* tag in CI.
set -e

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 TAG|-" >&2
  exit 2
fi

if [ "$1" = - ]; then
  message=$(cat)
else
  if [ "$(git cat-file -t "refs/tags/$1" 2>/dev/null)" != tag ]; then
    echo "The tag $1 isn't an annotated tag: its message is the release's notes (git tag -a $1)." >&2
    exit 1
  fi
  message=$(git for-each-ref --format='%(contents)' "refs/tags/$1")
fi

found=$(printf '%s\n' "$message" | awk '
  /^[ \t]*(```|~~~)/ { fence = !fence; next }
  fence { next }
  {
    s = $0
    gsub(/`[^`]*`/, "", s)
    if (s ~ /(^|[^A-Za-z0-9_.`\/@+-])@[A-Za-z0-9]/) print "  line " NR ": " $0
  }')

if [ -n "$found" ]; then
  echo "The tag message mentions someone on GitHub, where it becomes the release's notes;" \
       "write the word as code (\`@name\`) or reword it:" >&2
  printf '%s\n' "$found" >&2
  exit 1
fi
