"""kittable - the library behind the Kit/Table view. The source: edit it here (docs/TABLE-KIT.md).

Every binding transform and event script of the Kit views is one call into this module; tools/kit_table.py builds only the
views. Per-call timings go to the 'kittable' logger, so the cost of each interaction can be read from the gateway log.
"""
import threading
import time

LOG = system.util.getLogger("kittable")

SOURCE_TTL = 30        # seconds the rows a source returned are kept, so a group, count or export does not query again
CACHE_MAX = 4          # sources kept at once
GROUP_ROW_CAP = 5000   # most data rows a grouped table sends; groups past it stay collapsed behind a notice
SLOTS = 16             # most columns the header row has room for
MAX_FIXED = 1000       # fixed widths are scaled down to this total, so the columns always fit and never scroll sideways
AGGS = ("none", "count", "sum", "avg", "min", "max")
AGG_NAMES = {"none": u"None", "count": u"Count", "sum": u"Sum", "avg": u"Average", "min": u"Minimum", "max": u"Maximum"}
GROUP_OPEN = u"▼ "
GROUP_SHUT = u"▶ "
MORE_MARK = u"… "
LABEL_MAX = 60
NO_GROUP = u"__none__"
# the Java date pattern for each moment token a column's fmt uses (exports, totals and group headings are formatted here)
MOMENT_TO_JAVA = ((u"YYYY", u"yyyy"), (u"DD", u"dd"), (u"A", u"a"))

_cache = {}
_lock = threading.RLock()


def _ms(t0, what, extra=u""):
	"""Log how long a call took, in ms."""
	LOG.info(u"KT %s ms=%.1f %s" % (what, (time.time() - t0) * 1000.0, extra))


def _plain(x):
	"""Return x as plain Python lists, dicts and strings (a view param or custom value arrives wrapped)."""
	return system.util.jsonDecode(system.util.jsonEncode(x))


def _resolve(source):
	"""Return the library function a source name like 'Data.getRows' points at."""
	parts = source.split(".")
	obj = globals()[parts[0]]
	for p in parts[1:]:
		obj = getattr(obj, p)
	return obj


def _javaPattern(fmt):
	"""Return a Java date pattern for a moment-style format."""
	for a, b in MOMENT_TO_JAVA:
		fmt = fmt.replace(a, b)
	return fmt


def _text(value, d):
	"""Return a cell value as the text a person would read."""
	if value is None:
		return u""
	if d.get("type") == "time" and isinstance(value, (int, long, float)):
		return system.date.format(system.date.fromMillis(long(value)), _javaPattern(d.get("fmt") or u"DD/MM/YYYY HH:mm:ss"))
	if d.get("type") == "number" and isinstance(value, (int, long, float)):
		return u"{:,.2f}".format(value) if isinstance(value, float) else u"{:,}".format(value)
	return unicode(value)


def loadRows(source, args, tick, page=0, size=0, load=0):
	"""Return the source's rows, from the gateway's copy when it is under SOURCE_TTL old (one query per view load, tick or page)."""
	if not source:
		# Kit/Table opened on its own in the Designer has no source param
		return {"at": time.time(), "rows": [], "hay": None}
	# a Date in args would be encoded as text by _plain: pass it as epoch milliseconds
	args = _plain([x.getTime() if hasattr(x, "getTime") else x for x in (args or [])])
	if size:
		args = args + [int(page) * int(size), int(size)]
	key = (source, repr(args), tick, load)
	with _lock:
		hit = _cache.get(key)
		if hit and time.time() - hit["at"] < SOURCE_TTL:
			return hit
		t0 = time.time()
		rows = _resolve(source)(*args)
		entry = {"at": time.time(), "rows": rows, "hay": None}
		_cache[key] = entry
		while len(_cache) > CACHE_MAX:
			del _cache[min(_cache, key=lambda k: _cache[k]["at"])]
		_ms(t0, u"source", u"%s rows=%d" % (source, len(rows)))
		return entry


def _haystack(entry, defs):
	"""Return, per row, the lower-case text of each column, built once per read of the source."""
	if entry["hay"] is None:
		t0 = time.time()
		entry["hay"] = [[_text(r.get(d["field"]), d).lower() for d in defs] for r in entry["rows"]]
		_ms(t0, u"haystack", u"rows=%d" % len(entry["rows"]))
	return entry["hay"]


def _matching(entry, defs, search, filters=None):
	"""Return the rows that contain the search in any column and satisfy every column filter (a text the column contains)."""
	needle = (search or u"").strip().lower()
	filt = [(i, (filters or {}).get(d["field"], u"").strip().lower()) for i, d in enumerate(defs)]
	filt = [(i, t) for i, t in filt if t]
	if not needle and not filt:
		return entry["rows"]
	hay = _haystack(entry, defs)
	out = []
	for r, h in zip(entry["rows"], hay):
		if filt and not all(t in h[i] for i, t in filt):
			continue
		if needle and not any(needle in c for c in h):
			continue
		out.append(r)
	return out


def _sortSpec(cols):
	"""Return [(field, descending)] for the columns that carry a sort."""
	return [(c["field"], c.get("sort") == "descending") for c in cols if c.get("sort") in ("ascending", "descending")]


def _sorted(rows, cols):
	"""Return the rows sorted by the columns' sort (last key first, so the first key wins)."""
	out = list(rows)
	for field, desc in reversed(_sortSpec(cols)):
		out.sort(key=lambda r: _sortKey(r.get(field)), reverse=desc)
	return out


def _sortKey(v):
	"""Return a sort key that orders None first and text without regard to case."""
	if v is None:
		return (0, 0)
	if isinstance(v, basestring):
		return (1, v.lower())
	return (1, v)


def _aggregate(rows, field, fn):
	"""Return one aggregate of a column over rows as text."""
	if fn == "count":
		return u"count %s" % format(len(rows), ",")
	nums = [r.get(field) for r in rows if isinstance(r.get(field), (int, long, float))]
	if not nums:
		return u""
	val = {"sum": sum(nums), "avg": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}[fn]
	return u"%s %s" % (fn, u"{:,.2f}".format(val) if (isinstance(val, float) or fn == "avg") else u"{:,}".format(val))


def _visible(cols):
	"""Return the visible columns, in the order the table shows them."""
	return [c for c in cols if c.get("visible", True)]


def decorate(rows, defs):
	"""Return the rows with each chip column's value wrapped as a styled cell (the rows themselves when there is none)."""
	chips = [d for d in defs if d.get("type") == "chip"]
	if not chips:
		return rows
	out = []
	for r in rows:
		o = dict(r)
		for d in chips:
			v = r.get(d["field"])
			o[d["field"]] = {"value": v, "style": {"classes": "kit-chip kit-tone-" + (d.get("tones") or {}).get(v, "neutral")}}
		out.append(o)
	return out


def _groupLabels(entry, defs, group, search, filters):
	"""Return (label, rows) per group in order, for the rows the search and filters leave."""
	gdef = [d for d in defs if d["field"] == group][0]
	buckets = {}
	for r in _matching(entry, defs, search, filters):
		buckets.setdefault(r.get(group), []).append(r)
	return [((_text(k, gdef) or u"(blank)")[:LABEL_MAX], buckets[k]) for k in sorted(buckets, key=_sortKey)]


def shapeGroups(entry, defs, cols, group, collapsed, search, aggs, gsort, filters):
	"""Return the grouped table's data: a styled heading row per group, then its rows unless collapsed; the rows sent are capped."""
	if not cols:
		return []
	t0 = time.time()
	spec = [{"field": gsort.get("field"), "sort": gsort.get("dir", "none")}] if gsort.get("field") else []
	vis = _visible(cols)
	groups = _groupLabels(entry, defs, group, search, filters)
	shut = set(collapsed or [])
	total = sum(len(g) for _, g in groups)
	allShut = total > GROUP_ROW_CAP and not shut
	sent, hidden, out = 0, 0, []
	for label, g in groups:
		g = _sorted(g, spec)
		closed = allShut or label in shut or (sent + len(g) > GROUP_ROW_CAP)
		head = dict((c["field"], u"") for c in vis)
		head[vis[0]["field"]] = (GROUP_SHUT if closed else GROUP_OPEN) + label + u" (%s)" % format(len(g), ",")
		for c in vis[1:]:
			fn = aggs.get(c["field"], "none")
			if fn != "none":
				head[c["field"]] = _aggregate(g, c["field"], fn)
		out.append({"value": head, "style": {"classes": "kit-grouprow"}})
		if closed:
			hidden += len(g) if (sent + len(g) > GROUP_ROW_CAP and label not in shut and not allShut) else 0
		else:
			out.extend(decorate(g, defs))
			sent += len(g)
	if hidden:
		note = dict((c["field"], u"") for c in vis)
		note[vis[0]["field"]] = MORE_MARK + u"%s more rows not sent: narrow with search, or collapse groups" % format(hidden, ",")
		out.append({"value": note, "style": {"classes": "kit-noterow"}})
	_ms(t0, u"shape", u"rows=%d groups=%d sent=%d" % (total, len(groups), sent))
	return out


def getRows(v):
	"""Return the table's data: the rows as they are (the browser searches, filters and sorts) or, when grouped, shaped here."""
	if not v["ready"]:
		return []
	try:
		entry = loadRows(v["source"], v["args"], v["tick"], v["page"], v["size"], v["load"])
		defs = _plain(v["columns"])
		if not v["group"]:
			return decorate(entry["rows"], defs)
		sh = _plain(v["shape"])
		if not sh:
			return []
		return shapeGroups(entry, defs, sh["cols"], v["group"], sh["collapsed"], sh["search"], sh["aggs"], sh["gsort"], sh["filters"])
	except Exception as e:
		LOG.error(u"KT getRows failed: %s" % e)
		return []


def refreshShape(view):
	"""Set what the gateway shapes a grouped table from (only grouped scripts write it, so a header sort never resends the rows)."""
	if not view.custom.groupBy:
		if _plain(view.custom.shape):
			view.custom.shape = {}
		return
	view.custom.shape = {"cols": [{"field": c["field"], "visible": c.get("visible", True)} for c in _plain(view.custom.cols)],
		"collapsed": _plain(view.custom.collapsed), "search": view.custom.searchApplied or u"", "aggs": _plain(view.custom.aggs),
		"gsort": _plain(view.custom.gsort), "filters": _plain(view.custom.filters)}


def applySearch(view, enter):
	"""Take the search text as applied (Enter, or the text emptied): the count, any totals and a grouped table follow it."""
	if not enter and view.custom.search:
		return
	view.custom.searchApplied = view.custom.search or u""
	if view.custom.hasTotals:
		view.custom.cols = withTotals(_plain(view.custom.cols), view, _plain(view.custom.aggs))
	refreshShape(view)


def getInfo(v):
	"""Return {total, shown, text, empty, error, paged, newer, older} for the toolbar's count, paging and the empty state."""
	if not v["ready"]:
		return {"total": 0, "shown": 0, "text": u"", "empty": u"Loading…", "error": False, "paged": False, "newer": False, "older": False}
	try:
		entry = loadRows(v["source"], v["args"], v["tick"], v["page"], v["size"], v["load"])
		loaded = len(entry["rows"])
		search = (v["search"] or u"").strip()
		filters = _plain(v["filters"]) or {}
		narrowed = bool(search) or any(filters.values())
		shown = len(_matching(entry, _plain(v["columns"]), search, filters)) if narrowed else loaded
		whole = int(_resolve(v["countSource"])(*list(_plain(v["args"]) or []))) if v["size"] else loaded
		paged = bool(v["size"]) and whole > v["size"]
		if paged:
			lo = v["page"] * v["size"]
			text = u"Rows %s–%s of %s, newest first" % (format(lo + 1, ","), format(lo + loaded, ","), format(whole, ","))
			if narrowed:
				text += u" · %s match" % format(shown, ",")
			more = lo + loaded < whole
		else:
			more = False
			text = (u"%s of %s" % (format(shown, ","), format(loaded, ","))) if narrowed else u"%s rows" % format(loaded, ",")
		return {"total": whole, "shown": shown, "text": text, "empty": u"No rows" if not loaded else u"No rows match", "error": False, "paged": paged,
			"newer": v["page"] > 0, "older": more}
	except Exception as e:
		LOG.error(u"KT getInfo failed: %s" % e)
		return {"total": 0, "shown": 0, "text": u"", "empty": u"Could not read the rows: %s" % e, "error": True, "paged": False, "newer": False, "older": False}


def setPage(view, step):
	"""Move one page newer (-1) or older (+1); the group and totals follow the new rows."""
	view.custom.page = max(0, view.custom.page + step)
	view.custom.sel = []
	view.custom.collapsed = []
	if view.custom.hasTotals:
		view.custom.cols = withTotals(_plain(view.custom.cols), view, _plain(view.custom.aggs))
	refreshShape(view)


def buildColumns(defs, saved):
	"""Return the stock Table's columns for the host's definitions, laid over a user's saved layout."""
	defs = _plain(defs)
	lay = dict((s["field"], s) for s in (saved or {}).get("cols", []))
	order = [s["field"] for s in (saved or {}).get("cols", [])]
	defs.sort(key=lambda d: order.index(d["field"]) if d["field"] in order else len(order))
	cols = []
	for d in defs:
		t = d.get("type", "text")
		s = lay.get(d["field"], {})
		num = t == "number"
		c = {"field": d["field"], "visible": s.get("visible", not d.get("hidden")), "editable": False, "resizable": False, "sortable": False,
			"sort": s.get("sort", "none"), "align": "center", "justify": "right" if num else "left", "viewPath": "", "viewParams": {},
			"boolean": "checkbox", "number": "value", "progressBar": {}, "toggleSwitch": {}, "nullFormat": {}, "filter": {},
			"numberFormat": d.get("fmt", "0,0.00") if num else "0,0.##", "dateFormat": d.get("fmt", "DD/MM/YYYY HH:mm:ss"),
			"render": "number" if num else ("date" if t == "time" else "auto"),
			"header": {"title": d["title"], "justify": "right" if num else "left", "align": "center", "style": {"classes": ""}},
			"footer": {"title": u"", "justify": "right" if num else "left", "align": "center", "style": {"classes": ""}},
			"style": {"classes": ""}}
		c["width"] = s.get("width") or d.get("width") or 1
		cols.append(c)
	return fit(cols)


def fit(cols):
	"""Return the columns with one flexible column among the visible ones and the fixed widths scaled to fit (width 1 means 'the rest')."""
	vis = _visible(cols)
	for c in cols:
		c["strictWidth"] = c["width"] > 1
	fixed = sum(c["width"] for c in vis if c["strictWidth"])
	if fixed > MAX_FIXED:
		for c in vis:
			if c["strictWidth"]:
				c["width"] = max(60, int(c["width"] * MAX_FIXED / fixed))
	if vis and all(c["strictWidth"] for c in vis):
		vis[-1]["strictWidth"] = False
	return cols


def setFilters(cols, filters):
	"""Return the columns with the browser-side filter of each column set from the filters (a text the column contains)."""
	for c in cols:
		t = (filters or {}).get(c["field"], u"").strip()
		c["filter"] = {"enabled": True, "visible": "never", "string": {"condition": "contains", "value": t}} if t else {}
	return cols


def startView(view, session):
	"""Set up a Kit/Table when it opens: the columns, this user's saved layout (or the host's default grouping), then ready."""
	saved = _plain(session.custom.kitTables).get(view.params.key) if session.custom.kitTables else None
	cols = buildColumns(view.params.columns, saved)
	if saved:
		view.custom.dense = bool(saved.get("dense"))
		view.custom.aggs = saved.get("aggs", {})
		view.custom.filters = saved.get("filters", {})
	view.custom.tick = view.params.tick
	view.custom.loadId = long(time.time() * 1000)
	view.custom.cols = setFilters(cols, _plain(view.custom.filters))
	group = saved.get("group") if saved else view.params.defaultGroup
	if group and group != NO_GROUP:
		view.custom.groupBy = group
		view.custom.gsort = (saved or {}).get("gsort") or {"on": True, "field": u"", "dir": "none"}
		view.custom.cols = applyMode(_plain(view.custom.cols), _plain(view.custom.filters), group)
		refreshShape(view)
	view.custom.layoutSig = system.util.jsonEncode(_layout(view))
	try:
		loadRows(view.params.source, view.params.args, view.custom.tick, view.custom.page, view.params.pageSize, view.custom.loadId)
	except Exception as e:
		# the host's args can arrive after this script runs; the data binding reads again when they do
		LOG.info(u"KT warm-up skipped: %s" % e)
	view.custom.ready = True


def freezeSort(cols, grouped):
	"""Return the columns with sorting handed to the gateway while grouped (the heading rows would sort into the data)."""
	for c in cols:
		if grouped:
			c["sort"] = "none"
	return cols


def _layout(view):
	"""Return the layout worth keeping: density, grouping, totals, filters, and each column's visibility, order, sort and width."""
	cols = _plain(view.custom.cols)
	return {"dense": bool(view.custom.dense), "group": view.custom.groupBy or NO_GROUP, "gsort": _plain(view.custom.gsort), "aggs": _plain(view.custom.aggs),
		"filters": _plain(view.custom.filters),
		"cols": [{"field": c["field"], "visible": c.get("visible", True) or bool(c.get("hidBy")), "sort": c.get("sort", "none"), "width": c["width"] if c.get("strictWidth") else 0} for c in cols]}


def applyMode(cols, filters, group):
	"""Return the columns for the table's mode: ungrouped, the Table sorts and filters in the browser; grouped, the gateway does both.

	A browser filter left on while grouped would hide the heading rows, whose own cells hold no text of the filtered column.
	The grouped column is hidden while the grouping is on (its value is in the heading row) and comes back when it ends:
	hidBy marks a column hidden that way, and the saved layout keeps it as visible.
	"""
	grouped = bool(group)
	for c in cols:
		if grouped and c["field"] == group and c.get("visible", True):
			c["visible"], c["hidBy"] = False, True
		elif c.get("hidBy") and not (grouped and c["field"] == group):
			c["visible"] = True
			del c["hidBy"]
	cols = fit(cols)
	cols = freezeSort(cols, grouped)
	if grouped:
		for c in cols:
			c["filter"] = {}
		return cols
	return setFilters(cols, filters)


def saveView(view, session, savedSig):
	"""Keep this user's layout for the next page load, unless it is the layout the view just started with.

	Writing the session property makes the host's own bindings notify again, and the rows would be read and sent to the browser a second
	time, so the write that startView's own changes would cause is skipped.
	"""
	st = _layout(view)
	sig = system.util.jsonEncode(st)
	if sig == savedSig:
		return
	view.custom.layoutSig = sig
	all_ = _plain(session.custom.kitTables) or {}
	all_[view.params.key] = st
	session.custom.kitTables = all_


def _apply(st, cmd):
	"""Return the state {cols, aggs, gsort, filters} after one command; no I/O."""
	cols, aggs, gsort, filters = st["cols"], st["aggs"], st["gsort"], st["filters"]
	op, field, val = cmd["op"], cmd.get("field", u""), cmd.get("value", u"")
	idx = [c["field"] for c in cols].index(field) if field in [c["field"] for c in cols] else -1
	if op == "toggle" and idx >= 0:
		cols[idx]["visible"] = not cols[idx].get("visible", True)
	elif op == "hide" and idx >= 0:
		cols[idx]["visible"] = False
	elif op == "sort" and gsort.get("on"):
		gsort = {"on": True, "field": field if val != "none" else u"", "dir": val}
	elif op == "sort":
		for c in cols:
			c["sort"] = val if c["field"] == field and val in ("ascending", "descending") else "none"
	elif op == "first" and idx >= 0:
		cols.insert(0, cols.pop(idx))
	elif op in ("left", "right") and idx >= 0:
		vis = [i for i, c in enumerate(cols) if c.get("visible", True)]
		at = vis.index(idx) if idx in vis else -1
		to = at - 1 if op == "left" else at + 1
		if at >= 0 and 0 <= to < len(vis):
			cols[idx], cols[vis[to]] = cols[vis[to]], cols[idx]
	elif op in ("wider", "narrower") and idx >= 0 and cols[idx]["width"] > 1:
		cols[idx]["width"] = int(cols[idx]["width"] * 1.25) if op == "wider" else max(60, int(cols[idx]["width"] * 0.8))
	elif op == "agg":
		aggs = dict(aggs)
		aggs[field] = val
		aggs = dict((k, x) for k, x in aggs.items() if x != "none")
	elif op == "filter":
		filters = dict(filters)
		filters[field] = val
		filters = dict((k, x) for k, x in filters.items() if x)
	return {"cols": fit(cols), "aggs": aggs, "gsort": gsort, "filters": filters}


def _state(view):
	"""Return the view's column state as plain values."""
	return {"cols": _plain(view.custom.cols), "aggs": _plain(view.custom.aggs), "gsort": _plain(view.custom.gsort), "filters": _plain(view.custom.filters)}


def runCmd(view, cmd):
	"""Apply one command from a menu or the toolbar to the table's state."""
	if cmd["key"] != view.params.key:
		return
	op = cmd["op"]
	if op == "group":
		setGroup(view, cmd.get("value", u"") if cmd.get("value") != NO_GROUP else u"")
		return
	if op in ("expand", "collapse"):
		setAllGroups(view, op == "collapse")
		return
	if op == "dense":
		view.custom.dense = not view.custom.dense
		return
	if op == "export":
		exportCsv(view)
		return
	st = _apply(_state(view), cmd)
	view.custom.aggs, view.custom.gsort, view.custom.filters = st["aggs"], st["gsort"], st["filters"]
	view.custom.cols = withTotals(applyMode(st["cols"], st["filters"], view.custom.groupBy), view, st["aggs"])
	refreshShape(view)


def runMenuCmd(view, cmd):
	"""Apply the same command to the Columns popup's own copy, so its rows show the change at once."""
	if cmd["key"] != view.params.key or cmd["op"] not in ("toggle",):
		return
	st = _apply(_state(view), cmd)
	view.custom.cols = st["cols"]


def withTotals(cols, view, aggs):
	"""Return the columns with each column's footer showing its total over what the search and filters leave."""
	use = [c for c in cols if aggs.get(c["field"], "none") != "none"]
	for c in cols:
		c["footer"]["title"] = u""
	if not use or not view.custom.ready:
		view.custom.hasTotals = False
		return cols
	t0 = time.time()
	entry = loadRows(view.params.source, view.params.args, view.custom.tick, view.custom.page, view.params.pageSize, view.custom.loadId)
	defs = _plain(view.params.columns)
	rows = _matching(entry, defs, view.custom.searchApplied, _plain(view.custom.filters))
	for c in use:
		c["footer"]["title"] = _aggregate(rows, c["field"], aggs[c["field"]])
	view.custom.hasTotals = True
	_ms(t0, u"totals", u"rows=%d" % len(rows))
	return cols


def setGroup(view, field):
	"""Group by a column (empty for none). Grouped, the gateway sorts: the sort moves to custom.gsort and the Table's own sorting is cleared."""
	cols = _plain(view.custom.cols)
	field = field or u""
	was = bool(view.custom.groupBy)
	view.custom.sel = []
	view.custom.collapsed = []
	if field and not was:
		cur = _sortSpec(cols)
		view.custom.gsort = {"on": True, "field": cur[0][0] if cur else u"", "dir": ("descending" if cur and cur[0][1] else "ascending") if cur else "none"}
	elif was and not field:
		g = _plain(view.custom.gsort)
		for c in cols:
			c["sort"] = g.get("dir", "none") if c["field"] == g.get("field") else "none"
		view.custom.gsort = {"on": False, "field": u"", "dir": "none"}
	view.custom.groupBy = field
	view.custom.cols = applyMode(cols, _plain(view.custom.filters), field)
	refreshShape(view)


def setAllGroups(view, collapse):
	"""Collapse every group (or, with collapse False, expand them all)."""
	if not view.custom.groupBy:
		return
	if collapse:
		entry = loadRows(view.params.source, view.params.args, view.custom.tick, view.custom.page, view.params.pageSize, view.custom.loadId)
		view.custom.collapsed = [lab for lab, _ in _groupLabels(entry, _plain(view.params.columns), view.custom.groupBy, view.custom.searchApplied, _plain(view.custom.filters))]
	else:
		view.custom.collapsed = []
	refreshShape(view)


def toggleGroup(view, rowValue):
	"""Collapse or expand the group whose heading row was clicked; a data row does nothing. Return True when it was a heading row."""
	if not view.custom.groupBy:
		return False
	first = [c["field"] for c in _visible(_plain(view.custom.cols))][0]
	text = rowValue.get(first) if hasattr(rowValue, "get") else None
	if not isinstance(text, basestring) or text[:2] not in (GROUP_OPEN, GROUP_SHUT) or not text.endswith(u")"):
		return False
	label = text[2:].rsplit(u" (", 1)[0]
	view.custom.sel = []
	shut = list(view.custom.collapsed or [])
	if text[:2] == GROUP_OPEN:
		view.custom.collapsed = shut + [label]
	else:
		view.custom.collapsed = [s for s in shut if s != label]
	refreshShape(view)
	return True


def _flat(r):
	"""Return a row as a plain dict with each chip cell's styled value unwrapped."""
	return dict((k, v.get("value") if isinstance(v, dict) and "value" in v else v) for k, v in _plain(r).items())


def rowClick(view, session, page, rowValue, index, data):
	"""Row click: a heading row collapses or expands its group; a data row runs the host's rowAction.

	The Table's click event carries only the columns it shows, so the action gets the whole row: the one at the clicked index in the Table's data
	when its shown values agree, else the first row of the data that agrees (a sort in the browser can move the index).
	"""
	if toggleGroup(view, rowValue) or not view.params.rowAction:
		return
	row = _flat(rowValue)
	rows = _plain(data) or []
	for cand in ([rows[index]] if 0 <= index < len(rows) else []) + rows:
		full = _flat(cand)
		if all(full.get(k) == v for k, v in row.items() if k in full):
			row = full
			break
	try:
		_resolve(view.params.rowAction)(row, view, session, page)
	except Exception as e:
		LOG.error(u"KT rowAction %s failed: %s" % (view.params.rowAction, e))


def getSelectionText(sel):
	"""Return the toolbar text for the selected rows."""
	n = len(sel or [])
	return u"%d selected" % n if n else u""


def copySelection(view):
	"""Copy the selected rows, visible columns in their order, as tab-separated text."""
	cols = _visible(_plain(view.custom.cols))
	defs = dict((d["field"], d) for d in _plain(view.params.columns))
	lines = []
	for r in _plain(view.custom.sel):
		lines.append(u"\t".join(_text(r.get(c["field"]) if not isinstance(r.get(c["field"]), dict) else r[c["field"]].get("value"), defs[c["field"]]) for c in cols))
	system.perspective.copyToClipboard(u"\n".join(lines))


def getGroupOptions(columns):
	"""Return the Group by dropdown's options: (none), then the text and chip columns."""
	return [{"value": NO_GROUP, "label": u"(none)"}] + [{"value": d["field"], "label": d["title"]} for d in _plain(columns) if d.get("type", "text") in ("text", "chip")]


def getGroupChip(v):
	"""Return the text of the toolbar chip that names the grouping and removes it."""
	names = dict((d["field"], d["title"]) for d in _plain(v["columns"]))
	return u"Grouped by %s  \u00d7" % names.get(v["group"], v["group"]) if v["group"] else u""


def getFilterText(v):
	"""Return the filter text a column has now, for the column menu's filter box."""
	return (_plain(v["filters"]) or {}).get(v["field"], u"")


def getHeadSlots(v):
	"""Return SLOTS header cells for the visible columns: title, width, and a quiet marker for a sort or a filter."""
	defs = dict((d["field"], d) for d in _plain(v["columns"]))
	gsort = _plain(v["gsort"]) or {}
	filters = _plain(v["filters"]) or {}
	vis = _visible(_plain(v["cols"]))
	out = []
	for i in range(SLOTS):
		if i >= len(vis):
			out.append({"show": False, "w": 0, "grow": 0, "title": u"", "field": u"", "mark": u"", "num": False})
			continue
		c = vis[i]
		d = defs[c["field"]]
		srt = gsort.get("dir", "none") if (gsort.get("on") and gsort.get("field") == c["field"]) else c.get("sort", "none")
		mark = []
		if srt in ("ascending", "descending"):
			mark.append(u"asc" if srt == "ascending" else u"desc")
		if filters.get(c["field"]):
			mark.append(u"filtered")
		out.append({"show": True, "w": c["width"] if c.get("strictWidth") else 0, "grow": 0 if c.get("strictWidth") else 1, "title": d["title"],
			"field": c["field"], "mark": u" · ".join(mark), "num": d.get("type") == "number"})
	return out


def _item(key, text, op=u"", field=u"", value=u"", check=False, keep=False, kind=u"item"):
	"""Return one menu row's params."""
	return {"key": key, "text": text, "op": op, "field": field, "value": value, "check": check, "keep": keep, "kind": kind}


def getMenuInstances(v):
	"""Return the popup's repeater rows for the column menu, the Columns chooser or the Group by list."""
	key, mode = v["key"], v["mode"]
	defs = dict((d["field"], d) for d in _plain(v["columns"]))
	cols = _plain(v["cols"])
	gsort = _plain(v["gsort"]) or {}
	group = v["group"] or u""
	aggs = _plain(v["aggs"]) or {}
	if mode == "cols":
		return [_item(key, defs[c["field"]]["title"], "toggle", c["field"], u"", c.get("visible", True) or bool(c.get("hidBy")), True) for c in cols]
	if mode == "more":
		out = [_item(key, u"Density: compact" if v.get("dense") else u"Density: comfortable", "dense"), _item(key, u"Export CSV", "export")]
		if group:
			out += [_item(key, u"Expand all groups", "expand"), _item(key, u"Collapse all groups", "collapse")]
		out.append(_item(key, u"", kind=u"rule"))
		return _ruled(out + [_item(key, defs[c["field"]]["title"], "toggle", c["field"], u"", c.get("visible", True) or bool(c.get("hidBy")), True) for c in cols])
	if mode == "group":
		out = [_item(key, u"(none)", "group", u"", NO_GROUP, not group)]
		out += [_item(key, d["title"], "group", f, f, group == f) for f, d in ((c["field"], defs[c["field"]]) for c in cols) if d.get("type", "text") in ("text", "chip")]
		return out
	f = v["field"]
	d = defs[f]
	c = [x for x in cols if x["field"] == f][0]
	srt = gsort.get("dir", "none") if (gsort.get("on") and gsort.get("field") == f) else (c.get("sort", "none") if not gsort.get("on") else "none")
	out = [_item(key, u"Sort ascending", "sort", f, "ascending", srt == "ascending"), _item(key, u"Sort descending", "sort", f, "descending", srt == "descending")]
	if srt != "none":
		out.append(_item(key, u"Clear sort", "sort", f, "none"))
	out.append(_item(key, u"", kind=u"rule"))
	if d.get("type", "text") in ("text", "chip"):
		out.append(_item(key, u"Remove grouping" if group == f else u"Group by %s" % d["title"], "group", f, NO_GROUP if group == f else f, group == f))
		out.append(_item(key, u"", kind=u"rule"))
	out += [_item(key, u"Move to start", "first", f), _item(key, u"Move left", "left", f), _item(key, u"Move right", "right", f)]
	if c["width"] > 1:
		out += [_item(key, u"Wider", "wider", f, keep=False), _item(key, u"Narrower", "narrower", f)]
	out += [_item(key, u"Hide column", "hide", f), _item(key, u"", kind=u"rule")]
	for fn in (AGGS if d.get("type") == "number" else ("none", "count")):
		out.append(_item(key, u"Total: " + AGG_NAMES[fn].lower() if fn != "none" else u"Total: none", "agg", f, fn, aggs.get(f, "none") == fn))
	return _ruled(out)


def _ruled(items):
	"""Return the items with each rule folded into the next item as a line above it (a rule row would take a whole row's height)."""
	out, top = [], False
	for it in items:
		if it["kind"] == u"rule":
			top = True
			continue
		if top:
			it["kind"] = u"top"
			top = False
		out.append(it)
	return out


def openMenu(view, event, page, mode, field=u"", lastY=0):
	"""Open the column menu (at the three dots), the Columns chooser or the Group by list under the click (lastY: where the last mouse-opened menu began)."""
	cols = _plain(view.custom.cols)
	defs = dict((d["field"], d) for d in _plain(view.params.columns))
	filt = mode == "col" and defs[field].get("type", "text") in ("text", "chip")
	rows = getMenuInstances({"key": view.params.key, "mode": mode, "field": field, "cols": cols, "aggs": _plain(view.custom.aggs), "gsort": _plain(view.custom.gsort),
		"columns": _plain(view.params.columns), "group": view.custom.groupBy or u"", "dense": bool(view.custom.dense)})
	height = 16 + 30 * len(rows) + 5 * len([r for r in rows if r["kind"] == u"top"]) + (62 if filt else 0)
	pos = getPopupPosition(event, page, 260, min(height, 460), _keyboardAnchor(lastY, page, mode, field, cols), mode == "col")
	if mode == "col" and getattr(event, "pageY", 0):
		view.custom.menuY = int(event.pageY) + 14
	system.perspective.openPopup(u"kit-menu-" + view.params.key, u"Kit/TableMenu", params={"key": view.params.key, "mode": mode, "field": field,
		"columns": _plain(view.params.columns), "cols": cols, "aggs": _plain(view.custom.aggs), "gsort": _plain(view.custom.gsort),
		"group": view.custom.groupBy or u"", "filters": _plain(view.custom.filters), "filtering": filt, "dense": bool(view.custom.dense)}, position=pos, modal=True, overlayDismiss=True,
		showCloseIcon=False, draggable=False, resizable=False)


def exportCsv(view):
	"""Send what the table shows as a CSV download: the applied search and the filters, the sort, the visible columns in their order.

	The search is searchApplied, as the count uses, not the text typed since. Grouped, the Table's own sort is off and the sort is gsort, so
	the rows go out in group order and by gsort inside each group, as the grouped table lists them. Collapsed groups are exported in full.
	"""
	t0 = time.time()
	cols = _plain(view.custom.cols)
	defs = dict((d["field"], d) for d in _plain(view.params.columns))
	entry = loadRows(view.params.source, view.params.args, view.custom.tick, view.custom.page, view.params.pageSize, view.custom.loadId)
	rows = _matching(entry, _plain(view.params.columns), view.custom.searchApplied, _plain(view.custom.filters))
	group = view.custom.groupBy
	if group:
		gsort = _plain(view.custom.gsort) or {}
		rows = _sorted(rows, [{"field": gsort.get("field"), "sort": gsort.get("dir", "none")}] if gsort.get("field") else [])
		rows = sorted(rows, key=lambda r: _sortKey(r.get(group)))
	else:
		rows = _sorted(rows, cols)
	# the grouped column is hidden while grouped (its value is in the heading rows, which a CSV has not got): it is exported in its place
	vis = [c for c in cols if c.get("visible", True) or c.get("hidBy")]
	ds = system.dataset.toDataSet([defs[c["field"]]["title"] for c in vis], [[_text(r.get(c["field"]), defs[c["field"]]) for c in vis] for r in rows])
	system.perspective.download(u"%s.csv" % (view.params.title or view.params.key or u"table").lower().replace(u" ", u"-"), system.dataset.toCSV(ds))
	_ms(t0, u"export", u"rows=%d" % len(rows))


def _keyboardAnchor(lastY, page, mode, field, cols):
	"""Return (x, y) to open a menu at: for a column menu x is the column's left edge, for the others a point near the toolbar's right.

	The view cannot read where it sits on the page, so the column's place is an estimate: the table is taken to span the viewport less a
	30 px margin, fixed columns keep their widths and the flexible one takes the rest. A column menu opens there for a click and for Enter or
	Space alike, so the two agree. A real click remembers its height for the keyboard to use.
	"""
	vw = page.props.dimensions.viewport.width
	left, right = 29, vw - 29
	vis = _visible(cols)
	if mode != "col":
		return right - 120, 150
	fixed = sum(c["width"] for c in vis if c.get("strictWidth"))
	flexN = len([c for c in vis if not c.get("strictWidth")]) or 1
	flexW = max(60, (right - left - fixed) / flexN)
	x = left
	for c in vis:
		w = c["width"] if c.get("strictWidth") else flexW
		if c["field"] == field:
			return x, lastY or 188
		x += w
	return left, lastY or 188


def getPopupPosition(event, page, width, height, anchor=None, atColumn=False):
	"""Return a popup position that stays inside the viewport: under the click, or under the anchor when the click has no position.

	atColumn puts the popup's left edge on the anchor's x (a column menu opens on its column's left edge), whatever the click's x.
	"""
	vw = page.props.dimensions.viewport.width
	vh = page.props.dimensions.viewport.height
	x = getattr(event, "pageX", None)
	y = getattr(event, "pageY", None)
	if not x and not y and anchor:
		x, y = anchor[0], anchor[1] - 14
	x = 120 if x is None else x
	y = 160 if y is None else y
	left = anchor[0] if atColumn and anchor else x - 20
	return {"left": int(max(8, min(left, vw - width - 8))), "top": int(max(8, min(y + 14, vh - height - 8))), "width": width, "height": height}
