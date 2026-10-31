# Contributing to Zone

Bug reports, questions and ideas are welcome in the issue tracker. Zone is
small on purpose (see [Why Zone](README.md#why-zone)), so an idea may be
declined because it doesn't fit, not because it's a bad one.

## Reporting a bug

Open an issue with the **Bug report** template. It asks for:

- **Zone's version**: press `?` in Zone (the shortcuts dialog shows it at the
  bottom, e.g. `Zone 1.1.0 · c28ec9e 2026-09-26 19:22`), or open
  `/api/version`.
- **How you run it**: `run.sh` with Podman or Docker, or without a
  container; the OS; the browser, and whether it's a phone.
- **Steps to reproduce**, what you expected and what happened. A few lines of
  Markdown or a `[[deck …]]` line that shows the problem help most.
- **The server log** around the error if there was one (`./run.sh logs`).

Leave out passwords, MCP tokens and anything private from your
notes. To report a security problem, don't open a public issue: contact the
maintainer directly.

## Suggesting a feature

Open an issue with the **Feature request** template and describe the problem
you want solved before the solution you have in mind. Check it against
Zone's principles in [AGENTS.md](AGENTS.md#goals--principles): no database,
no build step, Markdown files as the source of truth, one process, one person.

## Changing the code

Talk about a bigger change in an issue first, so no work gets lost on
something that won't be merged. Then:

1. Read [AGENTS.md](AGENTS.md): the constraints and conventions every change
   follows. Zone is written by AI coding agents, and AGENTS.md is what they
   read too, so give it to yours.
2. Read the spec file for the area you change (`docs/spec/`), and update it
   in the same change, as well as the README if users see the difference.
3. Add tests for the new behaviour; a bug fix comes with a test that fails
   without it.
4. Run the checks before you open a pull request. CI runs the same commands:

   ```bash
   ./run.sh test     # unit and API tests
   ./run.sh e2e      # browser tests and the task logic tests
   ./run.sh leaks    # secret scan
   ```

5. Turn on the pre-commit hook once, so a commit containing a secret is
   refused: `git config core.hooksPath .githooks`.

Keep a pull request to one topic, with a commit message that says what
changes for the user. By contributing you agree that your work is released
under the [AGPL-3.0-or-later](LICENSE).
