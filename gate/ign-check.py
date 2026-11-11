#!/usr/bin/env python3
"""House-style checks for Ignition 8.3 projects stored as files: Jython scripts, Perspective
views, tag exports, named queries and resource manifests. Rules nothing published covers.

Usage: ign-check.py <repo-path> [--only ID,ID] [--list]

Prints `<path>:<line>: <ID> <message>` per finding, sorted. Exit 0 whatever it finds,
2 on a usage error; the caller decides what fails. Python 3.10+, stdlib only.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys

PRUNE = {"node_modules", "dist", "build", ".git", ".ignition-lint", ".claude"}
# Generated files that are never committed, for a target that is not a git work tree.
UNTRACKED = {"__pycache__", ".resources", ".DS_Store", "Thumbs.db"}
# Floats only: integer members are mostly counts and states, which have no unit.
ANALOGUE_TYPES = {"Float4", "Float8"}
TAG_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
HISTORY = re.compile(
    r"(?<![\w-])(?:(?<!\bis )(?<!\bare )(?<!\bbe )(?<!\bbeen )(?<!\bbeing )(?<!\bget )(?<!\bgets )"
    r"used to|previously|before v\d|turned out|turns out|at the time|we did|we then|"
    r"we decided|v\d+ did|verified \d\d?/\d\d?|as of \d\d?/\d\d?)\b|(?-i:\b(?:FIX|D)-\d+\b)",
    re.I)
COLOUR_VALUE = re.compile(
    r"\s*(?:#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})|(?:rgba?|hsla?)\(.*\))\s*$", re.I)
COLOUR_IN_CSS = re.compile(
    r"(?<![\w&])#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})\b|\b(?:rgba?|hsla?)\(", re.I)
# Designer default names that are not just the last segment of the type id
DEFAULT_NAMES = {"view": "embeddedview", "coord": "coordinatecontainer", "flex": "flexcontainer",
                 "breakpt": "breakpointcontainer", "column": "columncontainer",
                 "progress": "progressbar", "xy": "xychart", "timeseries": "timeserieschart",
                 "pie": "piechart"}


class Logical:
    """One logical line of source: where it starts and ends, its indent, and its text with strings blanked."""

    def __init__(self, start, indent):
        self.start, self.end, self.indent = start, start, indent
        self.masked, self.strings = [], []

    def finish(self, end):
        """Freeze the line once its last physical line is known."""
        self.end, self.masked = end, "".join(self.masked).strip()
        self.width = len(self.indent.expandtabs(8))
        self.only_strings = bool(re.fullmatch(r"(?:''\s*)+", self.masked))


def lex(text):
    """Split Python 2 or 3 source into logical lines and comments without compiling it."""
    text = text.replace("\r\n", "\n")
    lines, comments = [], []
    i, n, ln, depth = 0, len(text), 1, 0
    cur, at_start, has_code = None, True, False
    while i < n:
        if at_start:
            j = i
            while j < n and text[j] in " \t\f":
                j += 1
            if cur is None and j < n and text[j] not in "#\r\n":
                cur = Logical(ln, text[i:j])
            at_start, has_code, indent, i = False, False, text[i:j], j
            continue
        c = text[i]
        if c == "\n":
            if cur and depth == 0:
                cur.finish(ln)
                lines.append(cur)
                cur = None
            ln, i, at_start = ln + 1, i + 1, True
            continue
        if c == "\\" and text[i + 1:i + 2] == "\n":
            ln, i, at_start = ln + 1, i + 2, True
            continue
        if c == "#":
            j = text.find("\n", i)
            j = n if j < 0 else j
            comments.append((ln, text[i:j].rstrip(), not has_code, indent))
            i = j
            continue
        m = re.match(r"(?i)(?:[rbu]|ur|br|rb)?('''|\"\"\"|'|\")", text[i:i + 5])
        if m and (m.start(1) == 0 or not (i and (text[i - 1].isalnum() or text[i - 1] == "_"))):
            q, j = m.group(1), i + m.end()
            while j < n:
                if text[j] == "\\":
                    j += 2
                elif text.startswith(q, j):
                    j += len(q)
                    break
                elif len(q) == 1 and text[j] == "\n":
                    break
                else:
                    j += 1
            j = min(j, n)
            if cur:
                cur.masked.append("''")
                cur.strings.append(text[i:j])
            ln += text.count("\n", i, j)
            has_code, i = True, j
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(depth - 1, 0)
        if not c.isspace():
            has_code = True
        if cur:
            cur.masked.append(c)
        i += 1
    if cur:
        cur.finish(ln)
        lines.append(cur)
    return lines, comments


def owners(lines):
    """Pair each logical line with the top-level function, Class.method or class that holds it."""
    stack, out = [], []
    for lg in lines:
        while stack and stack[-1][0] >= lg.width:
            stack.pop()
        m = re.match(r"(?:async\s+)?(def|class)\s+(\w+)", lg.masked)
        if m:
            lg.scope = tuple(kind for _, kind, _ in stack)
            stack.append((lg.width, m.group(1), m.group(2)))
        out.append((lg, owner_name(stack)))
    return out


def owner_name(stack):
    """Name a scope stack the way a reader would look for it."""
    if not stack:
        return None
    if stack[0][1] == "class":
        if len(stack) > 1 and stack[1][1] == "def":
            return "%s.%s" % (stack[0][2], stack[1][2])
        return "class " + stack[0][2]
    return stack[0][2]


def where(owner):
    """Phrase an owner for a message."""
    return "in %s" % owner if owner else "at module level"


def string_body(literal):
    """Return the text inside a string literal."""
    m = re.match(r"(?is)[rbu]*('''|\"\"\"|'|\")(.*)$", literal)
    q, body = m.group(1), m.group(2)
    return body[:-len(q)] if body.endswith(q) else body


def comment_blocks(comments):
    """Group full-line comments that sit on consecutive lines."""
    blocks = []
    for c in comments:
        if not c[2]:
            continue
        if blocks and blocks[-1][-1][0] == c[0] - 1:
            blocks[-1].append(c)
        else:
            blocks.append([c])
    return blocks


def comment_owner(pairs, comment):
    """Owner of the code a comment sits in: module level at column 0, else the next logical line's."""
    if not comment[3]:
        return None
    for lg, owner in pairs:
        if lg.start >= comment[0]:
            return owner
    return pairs[-1][1] if pairs else None


def is_banner(comment):
    """A comment line drawn as a rule: a run of 4+ repeated punctuation making up most of it."""
    body = comment.replace(" ", "")
    if not re.search(r"([^\w\s])\1{3,}", body):
        return False
    return sum(not ch.isalnum() for ch in body) * 2 >= len(body)


def looks_like_code(comment):
    """True when a comment's text parses as a code statement rather than prose."""
    text = re.sub(r"^#+\s?", "", comment).strip()
    if not text or text.endswith((".", "?", "!")) or len(text) < 4:
        return False
    if re.match(r"(?:if|elif|for|while|with|def|class|try|except)\b.*:$", text):
        text += "\n    pass"
    elif re.match(r"(?:else|try|finally)\s*:$", text):
        return False
    elif re.match(r"print\s+[\w'\"]", text):
        text = text[5:].strip()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    if len(tree.body) != 1:
        return False
    node = tree.body[0]
    if isinstance(node, ast.Expr):
        # "Material (site)" parses as a call; code puts no space before the bracket
        return isinstance(node.value, ast.Call) and bool(re.search(r"\w\(", text))
    if isinstance(node, ast.Return):
        return node.value is not None
    return isinstance(node, (ast.Assign, ast.AugAssign, ast.Import, ast.ImportFrom, ast.FunctionDef,
                             ast.ClassDef, ast.If, ast.For, ast.While, ast.With))


def first_argument(masked, start):
    """Text of the first argument of a call whose opening bracket is at start."""
    depth = 0
    for j in range(start, len(masked)):
        ch = masked[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return masked[start + 1:j]
        elif ch == "," and depth == 1:
            return masked[start + 1:j]
    return masked[start + 1:]


class Repo:
    """The files of one repo, read once and shared by every rule."""

    def __init__(self, root):
        self.root = root
        self.files, self.dirs = [], []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in PRUNE)
            self.dirs.append((dirpath, dirnames, filenames))
            self.files.extend(os.path.join(dirpath, f) for f in sorted(filenames))
        self._json, self._lex = {}, {}

    def rel(self, path):
        """Path relative to the repo, with forward slashes."""
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    def json(self, path):
        """Parsed JSON for a file, or None when it is not valid JSON."""
        if path not in self._json:
            try:
                with open(path, encoding="utf-8") as fh:
                    self._json[path] = json.load(fh)
            except (OSError, ValueError):
                self._json[path] = None
        return self._json[path]

    def code_files(self):
        """Script-library code.py files."""
        return [f for f in self.files if f.endswith("/code.py") and "/script-python/" in f]

    def lexed(self, path):
        """Logical lines with owners, and comments, of a code.py."""
        if path not in self._lex:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines, comments = lex(fh.read())
            self._lex[path] = (owners(lines), comments)
        return self._lex[path]

    def views(self):
        """(path, parsed view) for every view.json that parses."""
        return [(f, self.json(f)) for f in self.files
                if f.endswith("/view.json") and isinstance(self.json(f), dict)]

    def view_scripts(self):
        """(path, label, script) for every script embedded in a view."""
        out = []
        for path, view in self.views():
            for label, comp in parts(view):
                for handler, script in scripts_of(comp):
                    out.append((path, "%s %s" % (label, handler), script))
        return out

    def scripts(self):
        """(rel path, [(logical line, owner)], view label) for code.py files (label None) and view scripts."""
        out = [(self.rel(f), self.lexed(f)[0], None) for f in self.code_files()]
        for path, label, script in self.view_scripts():
            out.append((self.rel(path), [(lg, label) for lg in lex(script)[0]], label))
        return out


def located(lg, owner, label):
    """Line and 'in X' phrase for a finding in a code.py (label None) or a view script."""
    return (1, "in " + label) if label else (lg.start, where(owner))


def parts(view):
    """(path, component) for the view itself and every component in it, root first."""
    out = [("view", view)]

    def walk(comp, path):
        out.append((path, comp))
        for child in comp.get("children") or []:
            if isinstance(child, dict):
                walk(child, path + "/" + str((child.get("meta") or {}).get("name", "?")))
    if isinstance(view.get("root"), dict):
        walk(view["root"], "root")
    return out


def scripts_of(comp):
    """(handler label, script) for each event, method, message handler, transform and onChange."""
    out = []
    for category in (comp.get("events") or {}).values():
        for name, events in (category or {}).items():
            for ev in events if isinstance(events, list) else [events]:
                script = ((ev or {}).get("config") or {}).get("script")
                if isinstance(script, str):
                    out.append((name, script))
    sc = comp.get("scripts") or {}
    for m in sc.get("customMethods") or []:
        out.append(("method %s" % m.get("name"), m.get("script") or ""))
    for m in sc.get("messageHandlers") or []:
        out.append(("message handler %s" % m.get("messageType"), m.get("script") or ""))
    for key, pc in sorted((comp.get("propConfig") or {}).items()):
        for t in ((pc.get("binding") or {}).get("transforms") or []):
            if t.get("type") == "script" and isinstance(t.get("code"), str):
                out.append(("transform on %s" % key, t["code"]))
        change = (pc.get("onChange") or {}).get("script")
        if isinstance(change, str):
            out.append(("onChange of %s" % key, change))
    return out


def expressions_of(comp):
    """(prop key, expression) for expression bindings, expression structures and expression transforms."""
    out = []
    for key, pc in sorted((comp.get("propConfig") or {}).items()):
        b = pc.get("binding") or {}
        cfg = b.get("config") or {}
        if b.get("type") == "expr" and isinstance(cfg.get("expression"), str):
            out.append((key, cfg["expression"]))
        if b.get("type") == "expr-struct":
            out.extend((key, e) for e in (cfg.get("struct") or {}).values() if isinstance(e, str))
        for t in b.get("transforms") or []:
            if t.get("type") == "expression" and isinstance(t.get("expression"), str):
                out.append((key, t["expression"]))
    return out


def check_s1(repo):
    """Code indented with spaces or a tab/space mix, once per function."""
    for rel, pairs, label in repo.scripts():
        seen = set()
        for lg, owner in pairs:
            if " " in lg.indent and owner not in seen:
                seen.add(owner)
                line, ctx = located(lg, owner, label)
                yield rel, line, "S1", "indented with spaces %s" % ctx


def check_s2(repo):
    """Public module-level function or method of a module-level class with no docstring, or a long summary."""
    for f in repo.code_files():
        pairs, _ = repo.lexed(f)
        for k, (lg, owner) in enumerate(pairs):
            m = re.match(r"(?:async\s+)?def\s+(\w+)", lg.masked)
            if not m or m.group(1).startswith("_") or lg.scope not in ((), ("class",)):
                continue
            nxt = pairs[k + 1][0] if k + 1 < len(pairs) else None
            if not nxt or nxt.width <= lg.width or not nxt.only_strings:
                yield repo.rel(f), lg.start, "S2", "%s has no docstring" % owner
                continue
            summary = re.split(r"\n\s*\n", string_body(nxt.strings[0]).strip(), maxsplit=1)[0]
            if len(summary.strip().splitlines()) > 1:
                yield repo.rel(f), nxt.start, "S2", "docstring summary of %s runs past one line" % owner


def check_s3(repo):
    """Banner comments, comment blocks over 3 lines, module docstrings over 5 lines."""
    for f in repo.code_files():
        pairs, comments = repo.lexed(f)
        rel = repo.rel(f)
        if pairs and pairs[0][0].only_strings and pairs[0][0].end - pairs[0][0].start + 1 > 5:
            yield rel, pairs[0][0].start, "S3", "module docstring over 5 lines"
        for block in comment_blocks(comments):
            owner = where(comment_owner(pairs, block[0]))
            banners = [c for c in block if is_banner(c[1])]
            if banners:
                yield rel, banners[0][0], "S3", "banner comment %s" % owner
            if len(block) > 3:
                yield rel, block[0][0], "S3", "comment block over 3 lines %s" % owner


def check_s4(repo):
    """History notes in comments and docstrings, and commented-out code."""
    for f in repo.code_files():
        pairs, comments = repo.lexed(f)
        rel = repo.rel(f)
        texts = [(c[0], c[1], comment_owner(pairs, c)) for c in comments]
        texts += [(lg.start, s, owner) for lg, owner in pairs if lg.only_strings for s in lg.strings]
        for line, text, owner in texts:
            for m in HISTORY.finditer(text):
                yield rel, line, "S4", 'history note "%s" %s' % (m.group(0), where(owner))
        for block in comment_blocks(comments):
            code = [c for c in block if looks_like_code(c[1])]
            if code:
                yield rel, code[0][0], "S4", "commented-out code %s" % where(comment_owner(pairs, code[0]))


def check_s5(repo):
    """Python 3 or Jython anti-idioms: print, import system, except X, e."""
    for rel, pairs, label in repo.scripts():
        for lg, owner in pairs:
            text = flat = lg.masked
            while re.search(r"\([^()]*\)", flat):
                flat = re.sub(r"\([^()]*\)", "_", flat)
            found = []
            if re.search(r"(?<![\w.])print\b(?!\s*=[^=])", text):
                found.append("print")
            if re.match(r"import\s+(?:.*,\s*)?system(?:[.\s,]|$)|from\s+system[.\s]", text):
                found.append("import system")
            if re.match(r"except\s+[^,:]+,\s*\w+\s*:", flat):
                found.append("except X, e")
            for what in found:
                line, ctx = located(lg, owner, label)
                yield rel, line, "S5", "%s %s" % (what, ctx)


def check_s6(repo):
    """system.db.run*Query with a SQL string built by +, % or .format()."""
    for rel, pairs, label in repo.scripts():
        for lg, owner in pairs:
            for m in re.finditer(r"\bsystem\.db\.(runQuery|runUpdateQuery|runScalarQuery)\s*\(", lg.masked):
                arg = first_argument(lg.masked, m.end() - 1)
                if "+" in arg or "%" in arg or ".format(" in arg:
                    line, ctx = located(lg, owner, label)
                    yield rel, line, "S6", "%s with a built SQL string %s" % (m.group(1), ctx)


def check_v1(repo):
    """Component left with the Designer's default name."""
    for path, view in repo.views():
        for cpath, comp in parts(view)[1:]:
            name = str((comp.get("meta") or {}).get("name", ""))
            seg = str(comp.get("type", "")).split(".")
            if cpath == "root" or len(seg) < 2:
                continue
            last = seg[-1].replace("-", "").lower()
            defaults = {last, DEFAULT_NAMES.get(last, last)}
            if seg[-2] == "container":
                defaults.add(last + "container")
            m = re.fullmatch(r"([A-Za-z]+)(?:_\d+)?", name)
            if m and m.group(1).lower() in defaults:
                yield repo.rel(path), 1, "V1", "%s has a default name" % cpath


def check_v2(repo):
    """Inline script over 5 statement lines."""
    for path, label, script in repo.view_scripts():
        body = [ln for ln in script.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if len(body) > 5:
            yield repo.rel(path), 1, "V2", "%s is a long inline script" % label


def blank(expr):
    """Expression with the inside of every string literal and {property reference} blanked, same length."""
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\{[^{}]*\}',
                  lambda m: m.group(0)[0] + " " * (len(m.group(0)) - 2) + m.group(0)[-1], expr)


def call_arguments(expr, masked, start):
    """Stripped text of each argument of the call whose opening bracket is at start."""
    args, depth, begin = [], 0, start + 1
    for j in range(start, len(masked)):
        ch = masked[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                args.append(expr[begin:j].strip())
                break
        elif ch == "," and depth == 1:
            args.append(expr[begin:j].strip())
            begin = j + 1
    return [] if args == [""] else args


def polling_problems(expr, scripted):
    """What makes an expression poll a script or read a tag by path; scripted when a script transform follows it."""
    masked, out = blank(expr), []
    calls = [(m.group(1), call_arguments(expr, masked, m.end() - 1))
             for m in re.finditer(r"(?<![\w.])(runScript|now|tag)\s*\(", masked)]
    for fn, args in calls:
        if fn == "tag":
            out.append("tag() in expression")
        elif fn == "runScript":
            if not args or not re.fullmatch(r"([\"'])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\1", args[0]):
                out.append("runScript() whose first argument is not a function path")
            if len(args) < 2:
                out.append("runScript() with no poll rate")
            elif args[1] != "0":
                out.append("runScript() polled at rate %s" % args[1])
    # now() with no argument polls every second; runScript re-runs whenever now() ticks.
    if scripted or any(fn == "runScript" for fn, _ in calls):
        if any(fn == "now" and (not args or args[0] != "0") for fn, args in calls):
            out.append("now() polls a script")
    return out


def check_v3(repo):
    """A binding that polls a script, or tag() in an expression."""
    for path, view in repo.views():
        for cpath, comp in parts(view):
            for key, expr in expressions_of(comp):
                b = (comp["propConfig"][key] or {}).get("binding") or {}
                scripted = any(t.get("type") == "script" for t in b.get("transforms") or [])
                for problem in sorted(set(polling_problems(expr, scripted))):
                    yield repo.rel(path), 1, "V3", "%s on %s %s" % (problem, cpath, key)


def colour_paths(node, keypath):
    """Key paths under a props style whose value holds a literal colour."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in ("params", "instances"):
                yield from colour_paths(v, "%s.%s" % (keypath, k))
    elif isinstance(node, list):
        for v in node:
            yield from colour_paths(v, keypath + "[]")
    # Style only: chart and symbol props hold colours the component writes itself.
    elif isinstance(node, str) and ".style." in keypath + "." and not node.lstrip().startswith("var("):
        if COLOUR_VALUE.match(node) or COLOUR_IN_CSS.search(re.sub(r"var\([^)]*\)", "", node)):
            yield keypath


def check_v4(repo):
    """Literal colour in a component's props."""
    for path, view in repo.views():
        for cpath, comp in parts(view)[1:]:
            for keypath in sorted(set(colour_paths(comp.get("props") or {}, "props"))):
                yield repo.rel(path), 1, "V4", "literal colour at %s %s" % (cpath, keypath)


def embeds(comp):
    """Static view paths a component embeds."""
    props, bound = comp.get("props") or {}, set(comp.get("propConfig") or {})
    out = []
    if comp.get("type") in ("ia.display.view", "ia.display.flex-repeater") and "props.path" not in bound:
        if isinstance(props.get("path"), str):
            out.append(props["path"])

    def walk(node, keypath):
        if keypath in bound:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "viewPath" and isinstance(v, str):
                    out.append(v)
                else:
                    walk(v, keypath + "." + k)
        elif isinstance(node, list):
            for v in node:
                walk(v, keypath)
    walk(props, "props")
    return [p.strip("/") for p in out if p.strip("/")]


def check_v5(repo):
    """View whose chain of embedded views is more than 3 deep."""
    graph, files = {}, {}
    for path, view in repo.views():
        m = re.match(r"(.*)/com\.inductiveautomation\.perspective/views/(.+)/view\.json$", path)
        if not m:
            continue
        key = (m.group(1), m.group(2))
        files[key] = path
        graph[key] = sorted({(m.group(1), p) for _, c in parts(view)[1:] for p in embeds(c)})
    memo, active = {}, set()

    def chain(key):
        if key in memo:
            return memo[key]
        if key in active:
            return []
        active.add(key)
        best = []
        for child in graph[key]:
            if child in graph:
                sub = chain(child)
                if len(sub) + 1 > len(best):
                    best = [child[1]] + sub
        active.discard(key)
        memo[key] = best
        return best
    for key in sorted(graph):
        c = chain(key)
        if len(c) > 3:
            yield repo.rel(files[key]), 1, "V5", "embedded views nest too deep: %s" % " > ".join([key[1]] + c)


def check_v6(repo):
    """Property binding that reads another component's props."""
    for path, view in repo.views():
        for cpath, comp in parts(view):
            for key, pc in sorted((comp.get("propConfig") or {}).items()):
                b = pc.get("binding") or {}
                target = str((b.get("config") or {}).get("path", ""))
                if b.get("type") != "property":
                    continue
                m = re.match(r"(?:\.+/|/)[^.]*\.(\w+)|parent\.(\w+)", target)
                if m and "props" in m.groups():
                    yield repo.rel(path), 1, "V6", "%s %s binds to %s" % (cpath, key, target)


def tag_exports(repo):
    """(path, export) for JSON files shaped like a tag export."""
    out = []
    for f in repo.files:
        if not f.endswith(".json") or f.endswith(("/view.json", "/resource.json")):
            continue
        d = repo.json(f)
        tags = d.get("tags") if isinstance(d, dict) else None
        if isinstance(tags, list) and ("tagType" in d or any(isinstance(t, dict) and "tagType" in t for t in tags)):
            out.append((f, d))
    return out


def walk_tags(node, path, in_types=False, in_udt=False):
    """Yield (tag path, tag, inside _types_, inside a UDT definition) depth first."""
    for t in node.get("tags") or []:
        if not isinstance(t, dict):
            continue
        p = "%s/%s" % (path, t.get("name")) if path else str(t.get("name"))
        yield p, t, in_types, in_udt
        kind = t.get("tagType")
        if kind == "UdtInstance" and in_udt:
            continue
        yield from walk_tags(t, p, in_types or t.get("name") == "_types_",
                             in_udt or (in_types and kind == "UdtType"))


def check_t1(repo):
    """Tag, folder, UDT or member name that is not an identifier."""
    for f, d in tag_exports(repo):
        for p, t, _, _ in walk_tags(d, ""):
            if t.get("tagType") != "Provider" and not TAG_NAME.match(str(t.get("name", ""))):
                yield repo.rel(f), 1, "T1", "tag name is not an identifier: %s" % p


def units_set(node, path=""):
    """Member paths under a UDT instance that set their own engUnit."""
    out = set()
    for t in node.get("tags") or []:
        if isinstance(t, dict):
            p = "%s/%s" % (path, t.get("name")) if path else str(t.get("name"))
            if t.get("engUnit"):
                out.add(p)
            out |= units_set(t, p)
    return out


def instances(node, acc):
    """Map each typeId to the set of unit-bearing member paths of each of its instances."""
    for t in node.get("tags") or []:
        if isinstance(t, dict):
            if t.get("tagType") == "UdtInstance":
                acc.setdefault(str(t.get("typeId")), []).append(units_set(t))
            instances(t, acc)
    return acc


def check_t2(repo):
    """Float UDT member with no engineering unit, unless every instance of the type sets one."""
    exports, acc = tag_exports(repo), {}
    for _, d in exports:
        instances(d, acc)
    for f, d in exports:
        types = []
        for p, t, _, in_udt in walk_tags(d, ""):
            if t.get("tagType") == "UdtType":
                types.append(p)
            if not (in_udt and t.get("tagType") == "AtomicTag" and t.get("dataType") in ANALOGUE_TYPES) \
                    or t.get("engUnit"):
                continue
            home = max((u for u in types if p.startswith(u + "/")), key=len, default="")
            type_id = home.split("_types_/", 1)[-1]
            member = p[len(home) + 1:]
            found = acc.get(type_id, [])
            if not found or not all(member in s for s in found):
                yield repo.rel(f), 1, "T2", "UDT member has no engUnit: %s" % p


def check_q1(repo):
    """Named query with a QueryString parameter."""
    for f in repo.files:
        if f.endswith("/resource.json") and "/named-query/" in f:
            d = repo.json(f) or {}
            for p in ((d.get("attributes") or {}).get("parameters") or []):
                if isinstance(p, dict) and p.get("type") == "QueryString":
                    yield repo.rel(f), 1, "Q1", "QueryString parameter %s" % p.get("identifier")


def git_ignored(root, paths):
    """The paths git ignores, or those named in UNTRACKED when root is not a git work tree."""
    if not paths:
        return set()
    try:
        inside = subprocess.run(["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True).stdout.strip() == "true"
    except OSError:
        inside = False
    if not inside:
        return {os.path.normpath(p) for p in paths if os.path.basename(os.path.normpath(p)) in UNTRACKED}
    out = subprocess.run(["git", "-C", root, "check-ignore", "--stdin", "-z"], capture_output=True,
                         text=True, input="\0".join(paths) + "\0").stdout
    return {os.path.normpath(os.path.join(root, p)) for p in out.split("\0") if p}


def check_p1(repo):
    """Resource folder whose contents do not match its resource.json files list."""
    extra = []
    for dirpath, dirnames, filenames in repo.dirs:
        if "resource.json" not in filenames:
            continue
        manifest = os.path.join(dirpath, "resource.json")
        d = repo.json(manifest)
        if not isinstance(d, dict) or not isinstance(d.get("files"), list):
            continue
        declared = set(d["files"])
        rel = repo.rel(manifest)
        # auth.json is the WebDev stamp tool's input, deliberately left out of files.
        for name in sorted(set(filenames) - declared - {"resource.json", "auth.json"}):
            extra.append((rel, os.path.join(dirpath, name), "%s is not in files" % name))
        for name in sorted(set(dirnames) - declared):
            extra.append((rel, os.path.join(dirpath, name, ""), "%s/ is a directory inside a resource folder" % name))
        for name in sorted(declared - set(filenames) - set(dirnames)):
            yield rel, 1, "P1", "%s is declared but missing" % name
    ignored = git_ignored(repo.root, [p for _, p, _ in extra])
    for rel, p, msg in extra:
        if os.path.normpath(p) not in ignored:
            yield rel, 1, "P1", msg


RULES = {
    "S1": ("script indented with spaces or a tab/space mix", check_s1),
    "S2": ("public function with no docstring, or a summary over one line", check_s2),
    "S3": ("banner comment, comment block over 3 lines, module docstring over 5 lines", check_s3),
    "S4": ("history note or commented-out code in a comment or docstring", check_s4),
    "S5": ("print, import system, or except X, e", check_s5),
    "S6": ("system.db.run*Query with SQL built by +, % or .format()", check_s6),
    "V1": ("component left with the Designer's default name", check_v1),
    "V2": ("inline view script over 5 statement lines", check_v2),
    "V3": ("binding that polls a script, or tag() in an expression", check_v3),
    "V4": ("literal colour in component props", check_v4),
    "V5": ("chain of embedded views more than 3 deep", check_v5),
    "V6": ("property binding that reads another component's props", check_v6),
    "T1": ("tag, folder, UDT or member name that is not an identifier", check_t1),
    "T2": ("float UDT member with no engUnit, in the type or on every instance", check_t2),
    "Q1": ("named query with a QueryString parameter", check_q1),
    "P1": ("resource folder that does not match its resource.json files", check_p1),
}


def main(argv):
    """Parse arguments, run the rules, print findings."""
    ap = argparse.ArgumentParser(description="House-style checks for Ignition project files.")
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--only", help="comma-separated rule IDs")
    ap.add_argument("--list", action="store_true", help="list the rules")
    args = ap.parse_args(argv)
    if args.list:
        for rid, (desc, _) in RULES.items():
            print("%s\t%s" % (rid, desc))
        return 0
    if not args.repo or not os.path.isdir(args.repo):
        ap.print_usage(sys.stderr)
        return 2
    ids = [i.strip() for i in args.only.split(",")] if args.only else list(RULES)
    unknown = [i for i in ids if i not in RULES]
    if unknown:
        print("ign-check: unknown rule %s" % ",".join(unknown), file=sys.stderr)
        return 2
    repo = Repo(os.path.abspath(args.repo))
    findings = set()
    for rid in ids:
        findings.update(RULES[rid][1](repo))
    for rel, line, rid, msg in sorted(findings):
        print("%s:%d: %s %s" % (rel, line, rid, msg))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
