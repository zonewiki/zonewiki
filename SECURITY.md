# Security policy

A wiki holds what people would not publish, so Zonewiki takes a security problem
seriously. What Zonewiki protects against, and how, is in
[`docs/spec/security.md`](docs/spec/security.md) (and, for users, private
pages and groups, in [`docs/spec/users.md`](docs/spec/users.md)).

## Reporting a vulnerability

Don't open a public issue. Report it privately on GitHub instead:
[Report a vulnerability](https://github.com/zonewiki/zonewiki/security/advisories/new)
(the repository's **Security** tab).

Please include:

- Zonewiki's version (press `?` in Zonewiki, or open `/api/version`) and how you run
  it (the image, `run.sh`, or the binary; with a password, users or neither;
  `ZONE_PUBLIC`, a reverse proxy).
- What an attacker can do, and what they need first (a session, an MCP
  token, a user's role, a page they wrote, a link someone opens).
- Steps to reproduce, ideally a few `curl` lines or a page's Markdown.

You get an answer within a week. Once a fix is released, the advisory is
published, naming you unless you'd rather not be named.

## What counts

Anything that gets past a rule Zonewiki states, for example:

- reading or changing a private page, a group's page, a task or a list you
  aren't allowed to, through any door (the browser, `/mcp`, search, history,
  uploads, mail links);
- an MCP token or an assistant's connection doing more than it was given;
- a viewer or a visitor (`ZONE_PUBLIC`) changing anything;
- script running from a page's content, a task or a file;
- a way around the login, the Host or Origin checks, or the throttles;
- secrets (password hashes, tokens, the session key) reaching git, a log or
  a response.

Not a vulnerability: a wiki reachable from the network without a login when
`no-login = yes` was set on purpose, and what a person who can already
read the wiki's folder on the server can do.

## Supported versions

Fixes go into `main` and the next release: its binaries and its image
(`ghcr.io/zonewiki/zonewiki`). Older releases get no fixes of their own, so
run the latest one.
