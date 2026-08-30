"""Regenerate `aws4_shapes.txt` from draw.io's published stencil library.

Run by hand when draw.io ships new service icons. Not wired into the test suite: a check
that fetched its own reference would fail offline, and a list that updated itself would stop
being the fixed thing the diagram checker is measured against.

Extracts shape NAMES only. The artwork stays where it is licensed to be -- see README.md.
"""
import os
import re
import sys
import urllib.request

SOURCE = ("https://raw.githubusercontent.com/jgraph/drawio/dev"
          "/src/main/webapp/stencils/aws4.xml")
TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aws4_shapes.txt")


def main():
    try:
        with urllib.request.urlopen(SOURCE, timeout=120) as response:
            body = response.read().decode("utf-8", "replace")
    except OSError as error:
        print(f"cannot fetch {SOURCE}: {error}", file=sys.stderr)
        return 2

    names = sorted({name.strip().lower().replace(" ", "_")
                    for name in re.findall(r'<shape[^>]*\sname="([^"]+)"', body)})
    names = [name for name in names if name and name != "mxgraph.aws4"]
    if len(names) < 500:
        print(f"refusing to write {len(names)} names; the source looks wrong",
              file=sys.stderr)
        return 1

    with open(TARGET, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(names) + "\n")
    print(f"wrote {len(names)} shape names to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
