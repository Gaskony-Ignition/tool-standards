#!/usr/bin/env python3
"""Run ign-check.py over each rule's fixtures: bad/ must give every line in expect.txt, good*/ nothing.

Each fixture tree is copied to a temporary folder first. A tree with a `gitignore` file
becomes a git work tree with that file as its .gitignore; any other tree is not in git.

Usage: python3 tests/run.py        Exit 1 if any rule fails.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "..", "gate", "ign-check.py")
FIXTURES = os.path.join(HERE, "fixtures")


def findings(rule, path):
    """Finding lines from running one rule over a copy of one fixture tree."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = os.path.join(tmp, "tree")
        shutil.copytree(path, tree)
        if os.path.exists(os.path.join(tree, "gitignore")):
            os.rename(os.path.join(tree, "gitignore"), os.path.join(tree, ".gitignore"))
            subprocess.run(["git", "init", "-q", tree], check=True)
        out = subprocess.run([sys.executable, CHECK, tree, "--only", rule],
                             capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def main():
    """Check every rule that has fixtures; print one line per rule."""
    listed = subprocess.run([sys.executable, CHECK, "--list"], capture_output=True, text=True, check=True).stdout
    failed = 0
    for rule in [line.split("\t")[0] for line in listed.splitlines()]:
        base = os.path.join(FIXTURES, rule)
        if not os.path.isdir(base):
            print("FAIL %s no fixtures" % rule)
            failed += 1
            continue
        with open(os.path.join(base, "expect.txt")) as fh:
            wanted = [line.strip() for line in fh if line.strip()]
        bad = findings(rule, os.path.join(base, "bad"))
        good = [f for d in sorted(os.listdir(base)) if d.startswith("good")
                for f in findings(rule, os.path.join(base, d))]
        missing = [w for w in wanted if not any(w in f for f in bad)]
        if missing or good:
            failed += 1
            print("FAIL %s missing %s; good gave %s" % (rule, missing, good))
        else:
            print("ok   %s %d bad finding(s), good clean" % (rule, len(bad)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
