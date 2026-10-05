"""Fails when line coverage of the core packages drops below the target."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET  # noqa: S405 - parses our own coverage report

PACKAGES = ("app/eta", "app/graph", "app/solver", "app/plan")
TARGET = 0.85


def main(path: str) -> int:
    root = ET.parse(path).getroot()  # noqa: S314
    totals = {p: [0, 0] for p in PACKAGES}
    for cls in root.iter("class"):
        filename = cls.get("filename", "")
        normalised = filename if filename.startswith("app/") else f"app/{filename}"
        pkg = next((p for p in PACKAGES if normalised.startswith(p + "/")), None)
        if pkg is None:
            continue
        for line in cls.iter("line"):
            totals[pkg][1] += 1
            if int(line.get("hits", "0")) > 0:
                totals[pkg][0] += 1
    failed = False
    for pkg, (hit, total) in totals.items():
        ratio = hit / total if total else 1.0
        status = "ok" if ratio >= TARGET else "FAIL"
        failed |= ratio < TARGET
        print(f"{pkg:12s} {ratio:6.1%} ({hit}/{total}) {status}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "coverage.xml"))
