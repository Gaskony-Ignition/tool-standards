#!/usr/bin/env python3
"""Read and check the `vendored` key of a repo's lint.json: third-party source folders the gate does not lint.

Each entry is {"path": "<directory from the repo root>", "reason": "<why>"}. The key is not a way to
switch the gate off, so an entry that is not a real directory inside the repo, or that leaves nothing
to lint, fails the gate.

Usage: vendored.py <repo> <outfile>   Checks the entries, prints one line per skipped folder, writes
the absolute paths to outfile (one per line). Exit 0 ok, 1 invalid.
Other gate scripts import load().
"""
import json
import os
import sys

PRUNE = {"node_modules", "dist", "build", ".git", ".ignition-lint", ".claude"}


def targets(repo, skip=()):
    """Number of view.json and script-library code.py files in the repo outside the skipped folders."""
    n = 0
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in PRUNE and os.path.join(root, d) not in skip]
        n += ("view.json" in files) + (("code.py" in files) and "/script-python/" in root + "/")
    return n


def count_files(path):
    """Files under a folder."""
    return sum(len(files) for _, _, files in os.walk(path))


def load(repo):
    """[(absolute path, reason)] of the repo's vendored folders; raises ValueError naming the first fault."""
    repo = os.path.abspath(repo)
    path = os.path.join(repo, "lint.json")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        entries = json.load(fh).get("vendored", [])
    if not isinstance(entries, list):
        raise ValueError("lint.json vendored must be a list of {path, reason}")
    real_repo, out = os.path.realpath(repo), []
    for e in entries:
        rel = e.get("path") if isinstance(e, dict) else None
        if not isinstance(rel, str) or not rel.strip():
            raise ValueError("lint.json vendored: every entry needs a path: %s" % (e,))
        if not isinstance(e.get("reason"), str) or not e["reason"].strip():
            raise ValueError("lint.json vendored: %s has no reason" % rel)
        norm = os.path.normpath(rel)
        if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/") or norm == ".":
            raise ValueError("lint.json vendored: %s must be a folder inside the repo, not the repo root, an absolute path or '..'" % rel)
        full = os.path.join(repo, norm)
        if not os.path.isdir(full):
            raise ValueError("lint.json vendored: %s is not a directory in the repo" % rel)
        if os.path.commonpath([real_repo, os.path.realpath(full)]) != real_repo:
            raise ValueError("lint.json vendored: %s resolves outside the repo" % rel)
        out.append((full, e["reason"].strip()))
    skip = {p for p, _ in out}
    if out and targets(repo) and not targets(repo, skip):
        raise ValueError("lint.json vendored: covers every view and script in the repo, so nothing is left to lint")
    return out


def main(argv):
    """Check the entries, print the skip lines, write the paths."""
    repo, outfile = os.path.abspath(argv[0]), argv[1]
    try:
        entries = load(repo)
    except (ValueError, OSError) as err:
        print("lint-gate: FAIL - %s" % err)
        return 1
    for full, reason in entries:
        print("lint-gate: vendored %s skipped, %d files - %s"
              % (os.path.relpath(full, repo), count_files(full), reason))
    with open(outfile, "w") as fh:
        fh.write("".join(p + "\n" for p, _ in entries))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
