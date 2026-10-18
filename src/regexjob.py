"""replace_in_pages' matching (mcp_tools), which for a regex runs in a
process of its own: Python's re has no time limit, and a pattern from an AI
client like (a+)+$ can backtrack for hours, holding a server thread and a
CPU. A process can be killed (subprocess.run's timeout), as grep_wiki's
git grep is. As a script it reads the job as JSON on stdin and writes the
result as JSON on stdout; it imports only mdtext, so it starts fast."""

import json
import re
import sys

from mdtext import replace_lines


def replace_in_texts(job: dict) -> dict:
    """{"hits": [[index, new text, changed lines], …]} for the texts that
    job["find"] (a regex; escaped for plain text) changes, or {"error": …}
    for a replacement that names a group the pattern doesn't have.
    job: texts, find, replace, regex (expand \\1 in replace), ignore_case,
    include_code (replace_lines)."""
    pattern = re.compile(job["find"], re.IGNORECASE if job["ignore_case"] else 0)

    def repl(m: re.Match) -> str:
        return m.expand(job["replace"]) if job["regex"] else job["replace"]

    hits = []
    try:
        for i, text in enumerate(job["texts"]):
            new, changed = replace_lines(text, pattern, repl, job["include_code"])
            if changed:
                hits.append([i, new, changed])
    except (re.error, IndexError) as e:
        return {"error": str(e)}
    return {"hits": hits}


if __name__ == "__main__":
    json.dump(replace_in_texts(json.load(sys.stdin)), sys.stdout)
