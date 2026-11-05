#!/usr/bin/env python3
"""Write public/vendor/mermaid/THIRD_PARTY_LICENSES: the license of every package
compiled into the vendored mermaid.min.js. Run it after updating Mermaid:

    python3 docs/licenses/mermaid_notices.py VERSION

The package list comes from the source map in Mermaid's npm package, so it names
what is in the bundle and the versions it was built with, not what a fresh install
would resolve to today. Only reads from the npm registry, and runs nothing from it.
"""

import base64
import hashlib
import io
import json
import re
import sys
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[2] / "public" / "vendor" / "mermaid"
BUNDLE = VENDOR / "mermaid.min.js"
OUT = VENDOR / "THIRD_PARTY_LICENSES"
LICENSE_FILE = re.compile(r"package/(licen[cs]e|copying|notice)[^/]*$", re.I)
LICENSE_IN_README = {"fastdom"}
# The bundle compiles elkjs. Zone, under the AGPL, takes it under its secondary
# license, GPL-3.0-or-later (COPYING.GPL-3.0 next to the notices), and both licenses
# ask for a way to get the source of what is distributed compiled.
SOURCE_NOTES = {
    "elkjs": "Zone distributes it under GPL-3.0-or-later (see COPYING.GPL-3.0 next to this\n"
             "file), in compiled form. Its source: https://github.com/kieler/elkjs\n"
             "(tag {version}), and the Eclipse Layout Kernel it is compiled from:\n"
             "https://github.com/eclipse/elk",
}


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def package(name: str, version: str) -> tuple[dict, tarfile.TarFile]:
    """A package's metadata and its tarball, checked against the registry's hash."""
    meta = json.loads(get(f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@')}/{version}"))
    data = get(meta["dist"]["tarball"])
    algorithm, want = meta["dist"]["integrity"].split("-", 1)
    if base64.b64encode(hashlib.new(algorithm, data).digest()).decode() != want:
        raise SystemExit(f"{name}@{version}: the tarball doesn't match its hash")
    return meta, tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")


def bundled(tar: tarfile.TarFile) -> list[tuple[str, str]]:
    """(name, version) of each package the source map lists, sorted."""
    sources = json.load(tar.extractfile("package/dist/mermaid.min.js.map"))["sources"]
    found = set()
    for source in sources:
        match = re.search(r"\.pnpm/([^/]+)/node_modules/((?:@[^/]+/)?[^/]+)/", source)
        if match:
            # The folder is name@version, then any peers or patch: @a+b@1.0.0_c@2.
            found.add((match[2], re.search(r"@(\d[^_@]*)", match[1])[1]))
    return sorted(found)


def license_texts(tar: tarfile.TarFile) -> list[str]:
    members = [m for m in tar.getmembers() if m.isfile() and LICENSE_FILE.match(m.name)]
    return [tar.extractfile(m).read().decode("utf-8").strip() for m in sorted(members, key=lambda m: m.name)]


def readme_license(tar: tarfile.TarFile) -> list[str]:
    """The README's License section, for a package that keeps its license there."""
    readme = tar.extractfile("package/README.md").read().decode("utf-8")
    match = re.search(r"^## License\n(.+?)(?=^## |\Z)", readme, re.M | re.S)
    return [match[1].strip()] if match else []


def main(version: str) -> None:
    _, tar = package("mermaid", version)
    vendored = BUNDLE.read_bytes()
    if tar.extractfile("package/dist/mermaid.min.js").read() != vendored:
        raise SystemExit(f"{BUNDLE} isn't the one in mermaid {version}: vendor that first")

    sections, missing = [], []
    for name, pkg_version in bundled(tar):
        meta, pkg = package(name, pkg_version)
        texts = readme_license(pkg) if name in LICENSE_IN_README else license_texts(pkg)
        if not texts:
            missing.append(f"{name}@{pkg_version}")
            continue
        head = [f"{name} {pkg_version}", f"License: {meta.get('license', 'see below')}"]
        repository = meta.get("repository")
        if isinstance(repository, dict) and repository.get("url"):
            head.append(f"Repository: {repository['url']}")
        if name in SOURCE_NOTES:
            head.append(SOURCE_NOTES[name].format(version=pkg_version))
        sections.append("\n".join(["=" * 78, *head, "=" * 78, "", "\n\n".join(texts)]))
    if missing:
        raise SystemExit("no license file in: " + ", ".join(missing))

    OUT.write_text(
        f"The packages compiled into mermaid.min.js (Mermaid {version}), whose own license\n"
        "is LICENSE next to this file, with their licenses. Each stays under its own.\n"
        "Written by docs/licenses/mermaid_notices.py from the bundle's source map.\n\n"
        f"mermaid.min.js sha256: {hashlib.sha256(vendored).hexdigest()}\n\n"
        + "\n\n".join(sections) + "\n",
        encoding="utf-8",
    )
    print(f"{OUT}: {len(sections)} packages")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
