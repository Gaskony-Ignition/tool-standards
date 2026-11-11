#!/usr/bin/env python3
"""Run ign-check.py over each rule's fixtures: bad/ must give every line in expect.txt, good*/ nothing.
Then run the whole gate over each case in tests/gate: bad/ must fail with every line in
expect.txt, good*/ must pass. The gate cases need uv and are skipped without it.

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
GATE = os.path.join(HERE, "..", "gate", "lint-gate.sh")
FIXTURES = os.path.join(HERE, "fixtures")
GATE_CASES = os.path.join(HERE, "gate")


def run_on_copy(path, cmd):
    """Exit code and stdout of cmd + [copy of the fixture tree at path]."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = os.path.join(tmp, "tree")
        shutil.copytree(path, tree)
        if os.path.exists(os.path.join(tree, "gitignore")):
            os.rename(os.path.join(tree, "gitignore"), os.path.join(tree, ".gitignore"))
            subprocess.run(["git", "init", "-q", tree], check=True)
        r = subprocess.run(cmd + [tree], capture_output=True, text=True)
    return r.returncode, r.stdout


def findings(rule, path):
    """Finding lines from running one rule over a copy of one fixture tree."""
    code, out = run_on_copy(path, [sys.executable, CHECK, "--only", rule])
    if code:
        raise SystemExit("ign-check.py exited %d on %s" % (code, path))
    return [line for line in out.splitlines() if line]


def wanted(base):
    """Non-blank lines of a fixture's expect.txt."""
    with open(os.path.join(base, "expect.txt")) as fh:
        return [line.strip() for line in fh if line.strip()]


def check_gate_cases():
    """Run the whole gate over each case in tests/gate; return the number that failed."""
    if not shutil.which("uvx"):
        print("skip gate cases: uvx not found")
        return 0
    failed = 0
    for case in sorted(os.listdir(GATE_CASES)):
        base = os.path.join(GATE_CASES, case)
        code, out = run_on_copy(os.path.join(base, "bad"), [GATE])
        missing = [w for w in wanted(base) if w not in out]
        good = [d for d in sorted(os.listdir(base)) if d.startswith("good")
                and run_on_copy(os.path.join(base, d), [GATE])[0] != 0]
        if code != 1 or missing or good:
            failed += 1
            print("FAIL gate %s bad exit %d, missing %s; failing good %s" % (case, code, missing, good))
        else:
            print("ok   gate %s" % case)
    return failed


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
        bad = findings(rule, os.path.join(base, "bad"))
        good = [f for d in sorted(os.listdir(base)) if d.startswith("good")
                for f in findings(rule, os.path.join(base, d))]
        missing = [w for w in wanted(base) if not any(w in f for f in bad)]
        if missing or good:
            failed += 1
            print("FAIL %s missing %s; good gave %s" % (rule, missing, good))
        else:
            print("ok   %s %d bad finding(s), good clean" % (rule, len(bad)))
    failed += check_gate_cases()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
