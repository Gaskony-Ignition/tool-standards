# Ignition project standard

What a project must look like when someone opens it in the Designer. Each rule carries a check ID that the gate enforces, or `review`, meaning a human judges it.

## Scripts

| Rule | Why | Check |
| --- | --- | --- |
| Indent with tabs, as the Designer's script editor does. | A tab and space mix is a syntax error to the linter. | S1 |
| camelCase functions, methods and arguments; UPPER_SNAKE constants; leading underscore for private. | Matches `system.*`. | pylint invalid-name (functions, arguments); rest review |
| One script package per project, named for the project. | Every call shows where it comes from. | review |
| Logic lives in the project library; an event handler, custom method or script transform is a call of at most five lines. | Library code is testable and reusable. | V2 |
| `system.util.getLogger("<package.module>")` once per module; no `print`. | Logs reach the gateway log with a name. | S5 |
| `except X as e`, never `except X, e`. | The comma form is Python 2 only. | S5 |
| Never `import system`. | `system` is already in scope. | S5 |
| No undefined names, unused imports or variables, unreachable code, or mutable default arguments. | These are faults, not style. | pylint |

## Views

| Rule | Why | Check |
| --- | --- | --- |
| Name every component for what it is for, in PascalCase; never leave a Designer default (`Label_0`). | Names show in bindings, scripts and the tree. | V1, NamePatternRule |
| camelCase custom properties, params, custom methods and message handlers. | One style across scripts and views. | NamePatternRule |
| Flex root by default; coordinate only for a fixed drawing. | Flex containers resize with the session. | review |
| Inputs in `params`, internal state in `custom`. | A view's interface is readable at a glance. | review |
| No binding from one component into another component's props; go through `view.custom` or a param. | Cross-component bindings break when either is moved. | V6 |
| No `getSibling`/`getParent` chains in scripts. | They break when the layout changes. | BadComponentReferenceRule |
| Pass a tag path into a view, not a value or a UDT object. | The view binds to the tag itself. | review |
| Embedded views no more than three deep. | Load time, per the ICC notes below. | V5 |
| Remove custom properties nothing reads. | Dead state misleads the next editor. | UnusedCustomPropertiesRule |
| No large datasets parked in custom properties. | They travel with the session. | ExcessiveContextDataRule |

## Bindings

| Rule | Why | Check |
| --- | --- | --- |
| Use the most direct binding that works: tag, indirect tag, expression, then script. | Simpler bindings are easier to read and cheaper to run. | review |
| A binding never polls a script: `runScript` only as `runScript("pkg.mod.fn", 0, args)`, and `now()` with a rate never feeds a script transform. | A polled script runs per component, per session, on the session's worker threads. | V3 |
| A value that is the same for every session comes from a tag: Alarm Metrics, or a tag a gateway timer writes. | Computed once for everyone. | review |
| No `tag()` in an expression; use an indirect tag binding. | The path and its subscription are visible. | V3 |
| A script transform calls one library function. | Keeps logic in the library. | V2 |
| No `now()` poll faster than 1000 ms. | Each tick re-evaluates the binding. | PollingIntervalRule |

## Styles

| Rule | Why | Check |
| --- | --- | --- |
| Colours come from style classes and theme variables, never a literal in a view. | A theme change reaches every view. | V4 |
| Style class names lowercase-with-dashes under the project's prefix. | Classes from different projects do not collide. | review |
| The project stylesheet declares `html { color-scheme: dark }` or `light`. | Without it Chrome's auto dark mode repaints SVG fills. | color-scheme |

## Tags and UDTs

| Rule | Why | Check |
| --- | --- | --- |
| Tag, folder and UDT member names are legal identifiers: letters, digits, underscore, no spaces. | Names carry straight into scripts and bindings. | T1 |
| A UDT for any equipment that repeats; a parent UDT for variants, not one type with unused members. | One definition to change. | review |
| UDT parameters, not hard-coded paths. | The type works for every instance. | review |
| Engineering units on every float UDT member. | Displays and history carry the unit. | T2 |

## Named queries and SQL

| Rule | Why | Check |
| --- | --- | --- |
| Named queries or `runPrep*` with `?` placeholders; never SQL built by concatenation or formatting. | Injection and quoting bugs. | S6 |
| No QueryString parameters. | They substitute text into the SQL. | Q1 |
| PascalCase query names, camelCase parameters. | Matches views and scripts. | review |

## Project

| Rule | Why | Check |
| --- | --- | --- |
| Every file in a resource folder is declared in its `resource.json`, and nothing declared is missing. | An undeclared file is undefined behaviour at import. | P1 |
| `.resources/` is gitignored; a `resource.json` is committed with the file it describes. | Generated state stays out of git. | review |
| Gateway event scripts, alarm pipelines and other runnable resources live in leaf projects, never an inheritable parent. | They would run once per child. | review |
| No passwords, tokens or keys in scripts, queries or properties. | They end up in git history. | review |

## Comments

Comments are short. A reader should see what a function does from its name and one line, and a comment should mark only what the code cannot say.

| Rule | Check |
| --- | --- |
| Every public function has a one-line docstring saying what it does or returns; the Designer shows it in autocomplete. | S2 |
| Add an `Args:` or `Returns:` line only where the name and signature do not already say it. | review |
| A `#` comment only for what the next person would get wrong: a platform trap, a unit, a non-obvious reason. One or two lines. | S3 (runs over three lines) |
| Module docstring: at most five lines on what the module is for. Design notes belong in `docs/`. | S3 |
| Never: section banners; history ("used to", "legacy", "v1 did"); ticket or fix numbers; verification stamps; commented-out code; a comment restating the line below it; usage-example footers. | S3, S4 |

Before:

```python
# ===== Setpoint writer =====
def writeSetpoint(path, sp):
	"""
	Writes a setpoint to a tag. This function checks the result, because
	ignoring it used to cause problems in v1.
	Args:
		path: the tag path
		sp: the setpoint
	"""
	# write the value
	results = system.tag.writeBlocking([path], [sp])
	# check the result
	return results[0].isGood()
```

After:

```python
def writeSetpoint(path, sp):
	"""Write sp to path; return True if the write was accepted."""
	# writeBlocking reports a failed write in its return value; it does not raise.
	return system.tag.writeBlocking([path], [sp])[0].isGood()
```

## Open rulings

Published guidance splits on these. This is what the standard chose.

1. Component type prefixes like `btn`: IA's Exchange style guide tolerates them, forum regulars reject them. We name by purpose with no prefix.
2. Script transform versus `runScript`: IA staff and forum benchmarks disagree on which is cheaper. We ban polling a script from a binding in either form, and allow `runScript` at poll rate 0.
3. The three-level embedded view limit comes from ICC table-talk notes, not IA documentation.

## Sources

- [IA 8.3 Version Control Guide](https://www.docs.inductiveautomation.com/docs/8.3/tutorials/version-control-guide)
- [Best practices for team environments](https://www.docs.inductiveautomation.com/docs/8.3/tutorials/version-control-guide/best-practices-for-team-environments)
- [Project Inheritance](https://www.docs.inductiveautomation.com/docs/8.3/platform/projects/project-inheritance)
- [Queries in scripting](https://www.docs.inductiveautomation.com/docs/8.3/platform/sql-in-ignition/queries-in-scripting)
- [Ignition Exchange Resources Style Guide](https://ia-exchange.inductiveautomation.com/docs/Exchange+Resources+Style+Guide.pdf)
- [DMC notes on Perspective load time](https://www.dmcinfo.com/blog/17187/optimizing-load-time-in-ignition-perspective-notes-from-icc-table-talks/)
- [ign-lint](https://github.com/bw-design-group/ignition-lint)
