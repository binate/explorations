#!/usr/bin/env python3
"""Move completed entries from claude-todo.md to claude-todo-done.md.

Each `### ` entry runs from its header line to just before the next
`### ` / `## ` / `---` boundary (or EOF). Done entries are conventionally
marked with a struck-through header, e.g.:

    ### ~~Short title~~ — FIXED 2026-06-03 (binate `abc1234`)

Usage:
    scripts/move_done.py                      # list candidates (struck headers)
    scripts/move_done.py "substr" ["substr"…] # move the matching entries
    scripts/move_done.py -n "substr"          # dry run (show, don't write)

Each substring must match exactly one `### ` header; the script aborts
without writing if any substring matches zero or several headers, so a
typo or a concurrent rename can't silently move the wrong entry. Matched
entries are inserted at the top of the `## Done` section, newest first.

Note: claude-todo.md is often edited concurrently. Run this, eyeball the
result, and commit promptly so the move doesn't sit in the working tree.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODO = os.path.join(ROOT, "claude-todo.md")
DONE = os.path.join(ROOT, "claude-todo-done.md")


def is_boundary(line):
    return (line.startswith("### ") or line.startswith("## ")
            or line.rstrip("\n") == "---")


def entry_span(lines, header_idx):
    """Return (start, end) for the entry beginning at header_idx; end is
    the index of the next boundary line (exclusive), or len(lines)."""
    for j in range(header_idx + 1, len(lines)):
        if is_boundary(lines[j]):
            return header_idx, j
    return header_idx, len(lines)


def main():
    ap = argparse.ArgumentParser(
        description="Move completed entries from claude-todo.md to "
                    "claude-todo-done.md.")
    ap.add_argument("substrings", nargs="*",
                    help="unique substring of each `### ` header to move; "
                         "with none given, list struck-through candidates")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="report what would move without writing")
    args = ap.parse_args()

    with open(TODO) as f:
        todo = f.readlines()
    headers = [i for i, l in enumerate(todo) if l.startswith("### ")]

    if not args.substrings:
        struck = [i for i in headers if "~~" in todo[i]]
        if not struck:
            print("no struck-through (done) entries in claude-todo.md")
            return 0
        print(f"{len(struck)} struck-through candidate(s):")
        for i in struck:
            print(f"  {i + 1}: {todo[i].rstrip()}")
        return 0

    # Resolve each substring to exactly one header.
    spans, moved = [], []
    for sub in args.substrings:
        hits = [i for i in headers if sub in todo[i]]
        if len(hits) != 1:
            print(f"ABORT: {sub!r} matched {len(hits)} headers", file=sys.stderr)
            for i in hits:
                print(f"  {i + 1}: {todo[i].rstrip()}", file=sys.stderr)
            return 1
        spans.append(entry_span(todo, hits[0]))
        moved.append(todo[hits[0]].rstrip("\n"))

    # Collect blocks (in todo order) and the remaining lines.
    spans.sort()
    drop = set()
    block = []
    for a, b in spans:
        block.extend(todo[a:b])
        drop.update(range(a, b))
    remaining = [l for i, l in enumerate(todo) if i not in drop]

    if args.dry_run:
        print(f"[dry run] would move {len(spans)} ent(y/ries):")
        for h in moved:
            print("  - " + h)
        return 0

    with open(DONE) as f:
        done = f.readlines()
    try:
        di = next(i for i, l in enumerate(done) if l.rstrip("\n") == "## Done")
    except StopIteration:
        print("ABORT: no '## Done' header in claude-todo-done.md", file=sys.stderr)
        return 1
    ins = di + 1
    while ins < len(done) and done[ins].strip() == "":
        ins += 1
    new_done = done[:ins] + block + done[ins:]

    with open(TODO, "w") as f:
        f.writelines(remaining)
    with open(DONE, "w") as f:
        f.writelines(new_done)

    print(f"moved {len(spans)} ent(y/ries); "
          f"todo {len(todo)}->{len(remaining)} lines, "
          f"done {len(done)}->{len(new_done)} lines")
    for h in moved:
        print("  - " + h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
