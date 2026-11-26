# Table kit

A toolbar, column menu and group rows round Ignition's stock Perspective Table. No module: three views (`Kit/Table`, `Kit/TableMenu`, `Kit/TableMenuItem`), one script library module and a block of stylesheet. This folder is the single source; a project holds a copy.

| File | What it is | Copy it to |
| --- | --- | --- |
| `kit_table.py` | Generates the three views. Its docstring is the contract a host table fills in (source function, columns, key, tick, paging, row action). | `tools/kit_table.py` |
| `kittable/code.py` | The library module every binding and event of the views calls. | `ignition/script-python/kittable/code.py` |
| `kit_rules.css` | The stylesheet block, `BEGIN table kit` to `END table kit`. The block's top comment lists the `--kit-*` tokens the project defines. | `tools/kit_rules.css`, appended to the project stylesheet by the project's own build |

## Copy it in, or refresh a copy

From a checkout of this repo at the tag the project's gate runs (`v1`), with `$KIT` as that checkout's `kit/table` and `$P` as the project repo:

```
cp $KIT/kit_table.py $KIT/kit_rules.css $P/tools/
cp $KIT/kittable/code.py $P/ignition/script-python/kittable/code.py
python3 $P/tools/kit_table.py $P        # rewrites the three Kit/* views
```

Then put `kit_rules.css` into the project stylesheet in place of the existing block (the project's stylesheet build does this) and scan the project onto the gateway. Do not edit a copy; change it here, release it, and refresh each project.

## Freshness check

Gate rule K1 runs on any repo with a `kittable` script package or a `Kit/Table` view. It fails when `kittable/code.py`, a `kit_table.py` or `kit_rules.css` copy, the stylesheet's table kit block, or any of the three views differs from this folder at the tag the gate is running from. The views are compared with what `kit_table.py` here generates, not with a stored copy.
