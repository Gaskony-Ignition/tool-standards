#!/usr/bin/env python3
"""Judge the gate's findings against a repo's lint.json exceptions and lint-baseline.json.

Reads the raw output that lint-gate.sh saved from ign-lint, pylint and ign-check,
adds the color-scheme check, and turns them all into one list of findings. Each
finding is an error (gates, never baselined), a warning (prints only) or style
(gates unless lint-baseline.json accepts it).

Usage: baseline.py <repo> <output-dir> [--write-baseline]     Exit: 0 pass, 1 fail.
"""
import ast
import collections
import io
import json
import os
import re
import sys
import tokenize

PRUNE = {"node_modules", "dist", "build", ".git", ".ignition-lint"}
PYLINT = re.compile(r"^(.+?):(\d+):\d+: ([EFWCRI])\d+: (.*) \(([a-z-]+)\)$")
IGN_CHECK = re.compile(r"^(.+?):(\d+): (\S+) (.*)$")
# Every W and C message the pylintrc enables is a style finding the baseline can hold.
STYLE_CATEGORIES = "WC"
# ign-lint's PROPERTY node covers every prop and nested key; the standard names only
# direct children of custom and params.
NAMED_PROPERTY = re.compile(r"(^|\.)(custom|params)\.[^.\[]+$")
# Perspective calls a script transform as transform(self, value, quality, timestamp);
# ign-lint 0.6.4 wraps it as transform(self, value), so pylint calls the other two undefined.
TRANSFORM_ARG = re.compile(r"^\S+\.binding\.transforms\[\d+\]: Line \d+: "
                           r"Undefined variable '(quality|timestamp)' \(undefined-variable\)")
UNUSED_PROPERTY = re.compile(r"^\S.*: (view parameter|custom property) '([^']+)' is defined but never referenced")
PERSPECTIVE = "com.inductiveautomation.perspective/"
NOISE = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
# Used when a script does not tokenize: the same reads and calls, comments and strings included.
RAW_ACCESS = re.compile(r"\.\s*(custom|params)\s*(?:\.\s*(\w+)|\[\s*[uU]?(['\"])(\w+)\3\s*\])")

Finding = collections.namedtuple("Finding", "source rule file line message severity raw ids")


def rel(repo, path):
    """Return path relative to the repo."""
    return os.path.relpath(path, repo) if os.path.isabs(path) else path


def find_line(repo, file, message):
    """Best-effort line of a view finding: the first line quoting the name it reports."""
    m = re.search(r"Name '([^']+)'", message)
    token = m.group(1) if m else re.sub(r"\[\d+\]", "", message.split(":", 1)[0]).rsplit(".", 1)[-1]
    try:
        with open(os.path.join(repo, file), encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if '"%s"' % token in line:
                    return n
    except OSError:
        pass
    return 1


def tokens(text):
    """(type, text) of each token of Python source but comments and layout, or None when it does not tokenize."""
    try:
        return [(t.type, t.string) for t in tokenize.generate_tokens(io.StringIO(text).readline)
                if t.type not in NOISE]
    except (tokenize.TokenError, SyntaxError):
        return None


def accesses(text):
    """(custom|params, name) for each .custom.<name>, .params.<name> or .params["<name>"] in source."""
    toks = tokens(text)
    if toks is None:
        return {(m.group(1), m.group(2) or m.group(4)) for m in RAW_ACCESS.finditer(text)}
    out = set()
    for i in range(len(toks) - 3):
        if toks[i][1] != "." or toks[i + 1][1] not in ("custom", "params") or toks[i + 1][0] != tokenize.NAME:
            continue
        nxt = toks[i + 2:i + 5]
        if nxt[0][1] == "." and nxt[1][0] == tokenize.NAME:
            out.add((toks[i + 1][1], nxt[1][1]))
        elif nxt[0][1] == "[" and nxt[1][0] == tokenize.STRING and len(nxt) > 2 and nxt[2][1] == "]":
            try:
                name = ast.literal_eval(nxt[1][1])
            except (ValueError, SyntaxError):
                continue
            if isinstance(name, str):
                out.add((toks[i + 1][1], name))
    return out


def calls_package(text, packages):
    """True when source calls a function of one of the script packages: pkg.mod.fn(...)."""
    toks = tokens(text)
    if toks is None:
        names = "|".join(re.escape(p) for p in packages)
        return bool(names and re.search(r"(?<![\w.])(?:%s)(?:\s*\.\s*\w+)+\s*\(" % names, text))
    for i, (kind, word) in enumerate(toks):
        if kind != tokenize.NAME or word not in packages or (i and toks[i - 1][1] == "."):
            continue
        j = i + 1
        while j + 1 < len(toks) and toks[j][1] == "." and toks[j + 1][0] == tokenize.NAME:
            j += 2
        if j > i + 1 and j < len(toks) and toks[j][1] == "(":
            return True
    return False


def view_scripts(node):
    """Every script in a view: event handlers, custom methods, message handlers, onChange and transforms."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and (k == "script" or (k == "code" and node.get("type") == "script")):
                yield v
            else:
                yield from view_scripts(v)
    elif isinstance(node, list):
        for v in node:
            yield from view_scripts(v)


def read_by_library(repo, file, message, cache):
    """True when the project's script library reads the unused property by name and the view calls that library.

    The project is the folder above com.inductiveautomation.perspective/; its library is
    ignition/script-python/ in the same folder, so projects side by side in one repo stay apart.
    """
    m = UNUSED_PROPERTY.match(message)
    if not m or "/" + PERSPECTIVE not in "/" + file:
        return False
    want = ("params" if m.group(1) == "view parameter" else "custom", m.group(2))
    project = ("/" + file).split("/" + PERSPECTIVE, 1)[0].lstrip("/")
    if project not in cache:
        lib = os.path.join(repo, project, "ignition", "script-python")
        packages, read = set(), set()
        for root, dirs, files in os.walk(lib):
            dirs[:] = sorted(d for d in dirs if d not in PRUNE)
            if root == lib:
                packages.update(dirs)
            if "code.py" in files:
                with open(os.path.join(root, "code.py"), encoding="utf-8", errors="replace") as fh:
                    read |= accesses(fh.read())
        cache[project] = (packages, read)
    packages, read = cache[project]
    if want not in read:
        return False
    if file not in cache:
        try:
            with open(os.path.join(repo, file), encoding="utf-8") as fh:
                view = json.load(fh)
        except (OSError, ValueError):
            view = {}
        cache[file] = any(calls_package(s, packages) for s in view_scripts(view))
    return cache[file]


def parse_ign_lint(repo, text):
    """Findings from ign-lint's console report."""
    out, path, section, rule, expect_path, cache = [], None, None, None, False, {}
    for line in text.splitlines():
        s = line.strip()
        if expect_path:
            path, section, rule, expect_path = rel(repo, s), None, None, False
        elif s.startswith("📄 Evaluating file"):
            expect_path = True
        elif s.startswith("❌ Found"):
            section = "error"
        elif s.startswith("⚠️"):
            section = "warning"
        elif re.match(r"^  (\w+Rule):$", line):
            rule = s[:-1]
        elif s.startswith("• ") and rule and path:
            msg = s[2:]
            if rule == "PylintScriptRule" and TRANSFORM_ARG.match(msg):
                continue
            # ign-lint 0.6.4 does not look into the script library, where rule V2 sends a view's logic.
            if rule == "UnusedCustomPropertiesRule" and read_by_library(repo, path, msg, cache):
                continue
            sym =re.search(r"\(([a-z-]+)\) \(([A-Z])\d+\)$", msg)
            ids = [rule] + ([sym.group(1)] if sym else [])
            if rule == "NamePatternRule":
                if "for property" in msg and not NAMED_PROPERTY.search(msg.split(":", 1)[0]):
                    continue
                severity = "style"
            elif sym and sym.group(2) in STYLE_CATEGORIES:
                severity = "style"
            else:
                severity = section
            out.append(Finding("ign-lint", ids[-1] if severity == "style" and sym else rule,
                               path, find_line(repo, path, msg), msg, severity, s, ids))
    return out


def parse_pylint(repo, text):
    """Findings from pylint's default text report."""
    out = []
    for line in text.splitlines():
        m = PYLINT.match(line.strip())
        if not m:
            continue
        path, n, cat, msg, sym = m.groups()
        severity = "error" if cat in "EF" else "style" if cat in STYLE_CATEGORIES else "warning"
        out.append(Finding("pylint", sym, os.path.relpath(os.path.abspath(path), repo), int(n), msg, severity, line.strip(), [sym]))
    return out


def parse_ign_check(text):
    """Findings from ign-check.py: <file>:<line>: <ID> <message>."""
    out, bad = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = IGN_CHECK.match(line)
        if m:
            file, n, rule, msg = m.groups()
            out.append(Finding("ign-check", rule, file, int(n), msg, "style", line, [rule]))
        else:
            bad.append(line)
    return out, bad


def color_scheme(repo):
    """Parentless Perspective projects whose stylesheet declares no html { color-scheme }."""
    out = []
    for root, dirs, _ in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in PRUNE)
        if os.path.basename(root) != "com.inductiveautomation.perspective":
            continue
        proj = os.path.dirname(root)
        try:
            with open(os.path.join(proj, "project.json")) as f:
                if json.load(f).get("parent"):
                    continue
        except (OSError, ValueError):
            pass
        css = os.path.join(root, "stylesheet", "stylesheet.css")
        if os.path.exists(css):
            with open(css) as f:
                if re.search(r"(?m)^html\s*\{[^}]*\bcolor-scheme\s*:", f.read()):
                    continue
            msg, file = "stylesheet declares no html { color-scheme }", rel(repo, css)
        elif any("view.json" in fs for _, _, fs in os.walk(os.path.join(root, "views"))):
            msg, file = "has views but no stylesheet, so no html { color-scheme }", rel(repo, root) + "/"
        else:
            continue
        out.append(Finding("color-scheme", "color-scheme", file, 1, msg, "error", msg, ["color-scheme"]))
    return out


def key(f):
    """Baseline key: rule, file and the message without positions that move on every edit."""
    msg = re.sub(r"\[\d+\]", "[]", f.message)
    msg = re.sub(r"\bLine \d+:", "Line:", msg)
    return (f.rule, f.file, msg)


def load_json(path, field, default):
    """Read one field of a JSON file, or the default when the file is absent."""
    if not os.path.exists(path):
        return default
    with open(path) as f:
        data = json.load(f)
    return data.get(field, default) if field else data


def write_baseline(path, findings):
    """Write findings as a sorted list, one entry per line, so merges stay readable."""
    rows = sorted(key(f) for f in findings)
    body = ",\n".join("  " + json.dumps({"rule": r, "file": fi, "message": m}) for r, fi, m in rows)
    with open(path, "w") as f:
        f.write("[\n%s\n]\n" % body if rows else "[]\n")


def escape(value, prop=False):
    """Escape a value for a GitHub workflow command."""
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return value.replace(":", "%3A").replace(",", "%2C") if prop else value


def main():
    """Run the judgement and print the report."""
    args = [a for a in sys.argv[1:] if a != "--write-baseline"]
    writing = len(args) < len(sys.argv) - 1
    repo, outdir = args
    fail = False

    rules = load_json(os.path.join(repo, "lint.json"), "exceptions", [])
    for r in rules:
        if not all(r.get(k) for k in ("file", "rule", "match", "reason")):
            sys.exit("lint.json: every exception needs file, rule, match and reason: %s" % r)

    def read(name):
        p = os.path.join(outdir, name)
        return open(p, encoding="utf-8").read() if os.path.exists(p) else ""

    findings = color_scheme(repo) + parse_ign_lint(repo, read("ign-lint.txt")) + parse_pylint(repo, read("pylint.txt"))
    checked, bad = parse_ign_check(read("ign-check.txt"))
    findings += checked
    for line in bad:
        print("lint-gate: ign-check printed a line the gate cannot read: %s" % line)
        fail = True

    used, excepted, live = set(), [], []
    for f in findings:
        hit = next((i for i, r in enumerate(rules)
                    if r["file"] in f.file and r["rule"] in f.ids and r["match"] in f.raw), None)
        if hit is None:
            live.append(f)
        else:
            used.add(hit)
            excepted.append((f, rules[hit]["reason"]))

    base_path = os.path.join(repo, "lint-baseline.json")
    style = [f for f in live if f.severity == "style"]
    errors = [f for f in live if f.severity == "error"]
    if writing:
        write_baseline(base_path, style)
        print("lint-gate: wrote %d findings to %s" % (len(style), base_path))
        if errors:
            print("lint-gate: %d errors not recorded - errors are never baselined" % len(errors))
    base = collections.Counter(tuple(e[k] for k in ("rule", "file", "message"))
                               for e in load_json(base_path, None, []))

    accepted, new, seen = [], list(errors), collections.Counter()
    for f in sorted(style, key=lambda f: (f.file, f.line)):
        k = key(f)
        seen[k] += 1
        (accepted if seen[k] <= base[k] else new).append(f)
    stale = sorted((base - seen).elements())

    def show(f, tag=""):
        print("%s:%d: %s %s%s" % (f.file, f.line, f.rule, f.message, tag))

    order = lambda f: (f.file, f.line, f.rule)
    for f in sorted((f for f in live if f.severity == "warning"), key=order):
        show(f, " (warning)")
    for f, reason in sorted(excepted, key=lambda x: order(x[0])):
        show(f, "   [excepted: %s]" % reason)
    for f in sorted(accepted, key=order):
        show(f, " (baseline)")
    for f in sorted(new, key=order):
        show(f)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("::error file=%s,line=%d,title=%s::%s"
                  % (escape(f.file, True), f.line, escape(f.rule, True), escape(f.message)))

    if stale:
        print("lint-gate: baseline is stale, run --write-baseline - these entries no longer match:")
        for r, fi, m in stale:
            print("  %s %s %s" % (fi, r, m))
    unused = [r for i, r in enumerate(rules) if i not in used]
    if unused:
        print("lint-gate: lint.json exceptions that no longer match anything - remove them:")
        for r in unused:
            print("  %s %s %r" % (r["file"], r["rule"], r["match"]))
    fail = fail or bool(new or stale or unused)
    print("lint-gate: %s - %d new, %d baseline, %d excepted"
          % ("FAIL" if fail else "pass", len(new), len(accepted), len(excepted)))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
