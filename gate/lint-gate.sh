#!/bin/bash
# lint-gate.sh - lint a repo's Perspective views and Jython scripts against the standard.
# Runs ign-lint over every view.json, pylint over every script-library code.py and
# ign-check.py over the repo, then baseline.py judges the findings against the repo's
# lint.json exceptions and lint-baseline.json.
#
# Usage: lint-gate.sh <repo-path> [--write-baseline]     Exit: 0 pass, 1 fail, 2 usage error.

set -uo pipefail

IGN_LINT_VERSION=0.6.4
PYLINT_VERSION=4.1.3
PYTHON_VERSION=3.13   # ign-lint requires Python below 3.14

usage() { echo "usage: lint-gate.sh <repo-path> [--write-baseline]" >&2; exit 2; }
repo="" write=()
for arg in "$@"; do
    case "$arg" in
        --write-baseline) write=(--write-baseline) ;;
        *) [ -z "$repo" ] || usage; repo="$arg" ;;
    esac
done
[ -n "$repo" ] && [ -d "$repo" ] || usage
repo=$(cd "$repo" && pwd)
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
command -v uvx >/dev/null || { echo "lint-gate: uvx not found (install uv)" >&2; exit 2; }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
python3 "$here/vendored.py" "$repo" "$tmp/vendored.txt" || exit 1
prune=(-name node_modules -o -name dist -o -name build -o -name .git -o -name .ignition-lint -o -name .claude)
# Vendored folders are not linted, but their script packages are still declared below.
vprune=("${prune[@]}")
while IFS= read -r v; do vprune+=(-o -path "$v"); done < "$tmp/vendored.txt"
mapfile -d '' views < <(find "$repo" \( "${vprune[@]}" \) -prune -o -name view.json -print0)
mapfile -d '' code < <(find "$repo" \( "${vprune[@]}" \) -prune -o -path '*/script-python/*/code.py' -print0)

# Script packages and Jython 2 builtins are declared as builtins so pylint does not call them undefined.
pkgs=$(find "$repo" \( "${prune[@]}" \) -prune -o -type d -path '*/script-python/*' -print \
    | sed -E 's#.*/script-python/([^/]+).*#\1#' | sort -u | paste -sd, -)
{ cat "$here/lint/jython.pylintrc"; printf '\n[VARIABLES]\nadditional-builtins=system,unicode,long,basestring,xrange,unichr,reduce,raw_input,file,cmp,execfile,apply,buffer,intern,coerce,reload%s\n' "${pkgs:+,$pkgs}"; } > "$tmp/pylintrc"
# lint.json "naming" picks the repo's case; the gate fails only names that break it.
python3 - "$here/lint/ign-lint.json" "$tmp" "$repo/lint.json" <<'PY' || exit 1
import json, os, re, sys
cfg = json.load(open(sys.argv[1]))
cfg["PylintScriptRule"]["kwargs"].update(pylintrc=sys.argv[2] + "/pylintrc", debug_dir=sys.argv[2] + "/debug")
naming = json.load(open(sys.argv[3])).get("naming", {}) if os.path.exists(sys.argv[3]) else {}
cases = {"components": ("PascalCase", "camelCase", "snake_case"),
         "properties": ("camelCase", "snake_case"), "functions": ("camelCase", "snake_case")}
for k, v in naming.items():
    if k not in cases or v not in cases[k]:
        sys.exit("lint-gate: lint.json naming.%s must be one of %s" % (k, ", ".join(cases.get(k, ()))) if k in cases
                 else "lint-gate: lint.json naming has no key %r (use %s)" % (k, ", ".join(cases)))
rules = cfg["NamePatternRule"]["kwargs"]["node_type_specific_rules"]
rules["component"]["convention"] = naming.get("components", "PascalCase")
rules["property"]["convention"] = naming.get("properties", "camelCase")
rules["custom_method"]["convention"] = naming.get("functions", "camelCase")
if naming.get("properties") == "snake_case":
    rules["message_handler"].update(convention="snake_case", pattern="^(?:[a-z][a-z0-9_]*|[a-z][a-z0-9]*(?:-[a-z0-9]+)+)$",
                                    pattern_description="snake_case, or kebab-case with a project prefix")
json.dump(cfg, open(sys.argv[2] + "/ign-lint.json", "w"))
if naming.get("functions") == "snake_case":
    rc = open(sys.argv[2] + "/pylintrc").read()
    # ign-lint wraps view scripts in Perspective's own camelCase handler names and arguments.
    rc = re.sub(r"(?m)^(function|method)-rgx=.*$",
                r"\1-rgx=(_?[a-z][a-z0-9_]*|__[a-z][a-z0-9_]*__|on[A-Z][a-zA-Z0-9]*|valueChanged)$", rc)
    rc = re.sub(r"(?m)^argument-rgx=.*$", "argument-rgx=(_?[a-z][a-z0-9_]*|previousValue|currentValue|missedEvents)$", rc)
    open(sys.argv[2] + "/pylintrc", "w").write(rc)
PY

fail=0
if [ ${#views[@]} -gt 0 ]; then
    echo "lint-gate: ${#views[@]} views"
    (cd "$tmp" && uvx -q --python "$PYTHON_VERSION" --from "ign-lint==$IGN_LINT_VERSION" \
        ign-lint --config "$tmp/ign-lint.json" --ignore-warnings "${views[@]}") > "$tmp/ign-lint.txt" 2>&1
    # ign-lint exits 1 on findings, which baseline.py judges; anything higher is a crash.
    [ $? -le 1 ] || { cat "$tmp/ign-lint.txt"; echo "lint-gate: ign-lint crashed"; fail=1; }
fi
if [ ${#code[@]} -gt 0 ]; then
    echo "lint-gate: ${#code[@]} script-library modules"
    uvx -q --python "$PYTHON_VERSION" --from "pylint==$PYLINT_VERSION" \
        pylint --rcfile "$tmp/pylintrc" "${code[@]}" > "$tmp/pylint.txt" 2>&1
    # Exit bit 32 is a usage error; the message bits are baseline.py's to judge.
    [ $(( $? & 32 )) -eq 0 ] || { cat "$tmp/pylint.txt"; echo "lint-gate: pylint usage error"; fail=1; }
fi
if [ -f "$here/ign-check.py" ]; then
    python3 "$here/ign-check.py" "$repo" > "$tmp/ign-check.txt" \
        || { echo "lint-gate: ign-check.py crashed"; fail=1; }
else
    echo "lint-gate: gate/ign-check.py not found - skipping its checks"
fi

python3 "$here/baseline.py" "$repo" "$tmp" "${write[@]}" || fail=1
exit $fail
