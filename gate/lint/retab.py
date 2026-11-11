#!/usr/bin/env python3
"""Rewrite tab-then-spaces indentation in Perspective view scripts as tabs only.

The Designer indents script bodies with tabs; hand-written continuation lines
often add spaces after the tab ("\\t    x"). Jython runs that, but Python 3 and
ign-lint reject the mix. Each group of four spaces after leading tabs becomes
one tab and any spaces left over are dropped. The raw file text is edited, so
Ignition's JSON escaping and layout stay as they are, and a script is only
rewritten when its syntax tree under
Jython's rules (tab = 8 columns) is unchanged and Python 3 now parses it.

Usage: retab.py <view.json>...     prints each file changed and any script skipped
"""
import ast
import json
import re
import sys

SCRIPT = re.compile(r'("(?:script|code)": ")((?:[^"\\]|\\.)*)(")')
LEAD = re.compile(r'(\\n(?:\\t)+)( +)')


def tree(src, jython):
    body, pad = (src.expandtabs(8), " " * 8) if jython else (src, "\t")
    return ast.dump(ast.parse("def _():\n" + "\n".join(pad + l for l in body.split("\n"))))


def fix(raw_value, where):
    new_raw = LEAD.sub(lambda m: m.group(1) + "\\t" * (len(m.group(2)) // 4), raw_value)
    if new_raw == raw_value:
        return raw_value
    old, new = json.loads('"%s"' % raw_value), json.loads('"%s"' % new_raw)
    try:
        same = tree(old, True) == tree(new, True)
        tree(new, False)
    except SyntaxError as e:
        print("  skipped %s: %s" % (where, e))
        return raw_value
    if not same:
        print("  skipped %s: syntax tree would change" % where)
        return raw_value
    return new_raw


for path in sys.argv[1:]:
    text = open(path, encoding="utf-8").read()
    out = SCRIPT.sub(lambda m: m.group(1) + fix(m.group(2), path) + m.group(3), text)
    if out != text:
        open(path, "w", encoding="utf-8").write(out)
        print(path)
