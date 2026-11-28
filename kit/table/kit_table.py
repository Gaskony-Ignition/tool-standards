#!/usr/bin/env python3
"""The table kit: a toolbar, a header and a row body round Ignition's stock Table, with no module.

Builds only the three views, under the project folder given (required):
  com.inductiveautomation.perspective/views/Kit/{Table,TableMenu,TableMenuItem}/view.json
    python3 tools/kit_table.py .            (build_views.py calls main() with the repo root)

The rest of the kit is edited in place, not generated:
  ignition/script-python/kittable/code.py   the one library module every binding and event of the views calls
  tools/kit_rules.css                       the stylesheet block, between "BEGIN table kit" and "END table kit"; build_css.py appends it

The views are generated because Kit/Table repeats one header cell 16 times. To use the kit in another project: copy this file,
kittable/code.py and kit_rules.css, run this file on the project folder, add the block to the project stylesheet, and define the
--kit-* tokens (the block's top comment lists them). The host supplies, per table instance:

  * a library function that returns the rows, as a list of dicts. It is named in the embed's
    `source` param ("Data.getRows") and called with the embed's `args` list. A time value
    is epoch milliseconds, so it sorts as a number and the Table formats it.
  * the column definitions, the embed's `columns` param: a list of
    {field, title, type, width, ...}. type is text, number, time or chip; width is pixels, 0 for
    "take the rest"; number takes `fmt` (Ignition number format), time takes `fmt` (moment tokens,
    DD/MM/YYYY HH:mm:ss), chip takes `tones` ({value: ok|warn|danger|info}).
  * `title` (the toolbar's heading), `key` (unique per instance on the page: it names the popup and
    the per-user saved layout), `tick` (anything that changes when the data should be re-read).
  * optional paging for a source bigger than the table should hold: `pageSize` (rows per page, 0 for none) and
    `countSource` (a library function returning the total, called with `args`). The source is then called with
    `offset, limit` added to `args`, newest page first, and the toolbar gets Newer / Older. Search, sort and
    grouping work inside the loaded page.
  * optional `rowAction`: a library function name, called as
    fn(row, view, session, page) when a data row is clicked, with the row as a plain dict (chip cells unwrapped). A group
    heading row still collapses; multi-select still works.
  * optional `hidden: true` on a column definition: the column starts hidden until the user ticks it in Columns.
  * a persistent session custom property `kitTables` (an object): where each user's density,
    hidden columns, column order, pins, sort, grouping and totals are kept across page reloads.

Cost model, so a host can judge it. Ungrouped, searching, sorting, filtering by column, resizing,
reordering and scrolling all happen in the browser, with no script call. The gateway runs the kit
only to (a) read the rows once per load or tick, (b) count the rows a search matches, on Enter,
(c) shape the rows when grouped (on a group, sort, collapse, search or totals change, the whole
shaped list is sent again), (d) work out totals, (e) export. The rows read in (a) are kept on the
gateway for SOURCE_TTL seconds, so (b) to (e) do not query the source again.
"""
import json
import os
import sys

# ---------------------------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------------------------
def comp(ctype, name, props=None, pos=None, binds=None, events=None, children=None, scripts=None, meta=None, tab=False):
    c = {"type": ctype, "meta": dict({"name": name}, **(meta or {})), "props": props or {}}
    if pos:
        c["position"] = pos
    if binds:
        c["propConfig"] = {k: {"binding": b} for k, b in binds.items()}
    if events:
        c["events"] = events
    if children is not None:
        c["children"] = children
    if scripts:
        c["scripts"] = scripts
    return c


def expr(e):
    return {"type": "expr", "config": {"expression": e}}


def prop(path, two=False):
    return {"type": "property", "config": {"path": path, "bidirectional": two}}


def indent(code):
    return "\n".join("\t" + ln for ln in code.split("\n"))


def script(code):
    return {"type": "script", "scope": "G", "config": {"script": indent(code)}}


def sb(inputs, code):
    return {"type": "expr-struct", "config": {"struct": inputs, "waitOnAll": True}, "transforms": [{"type": "script", "code": indent(code)}]}


def fix(px):
    return {"grow": 0, "shrink": 0, "basis": px}


def grow(n=1):
    return {"grow": n, "shrink": 1}


def flex(name, children, direction="row", classes="", pos=None, binds=None, align=None, gap=None, events=None, scripts=None, style=None):
    st = {"classes": classes} if classes else {}
    if gap:
        st["gap"] = "%dpx" % gap
    st.update(style or {})
    p = {"direction": direction, "style": st}
    if align:
        p["alignItems"] = align
    return comp("ia.container.flex", name, p, pos, binds, children=children, events=events, scripts=scripts)


def view(root, params=None, custom=None, binds=None, size=(900, 500), startup=None):
    v = {"custom": custom or {}, "params": params or {}, "props": {"defaultSize": {"width": size[0], "height": size[1]}}, "root": root}
    pc = {}
    for k in (params or {}):
        pc["params." + k] = {"paramDirection": "input", "persistent": False}
    for k, b in (binds or {}).items():
        pc[k] = {"binding": b}
    if pc:
        v["propConfig"] = pc
    if startup:
        v["events"] = {"system": {"onStartup": script(startup)}}
    return v


def handlers(msg, code):
    return {"customMethods": [], "extensionFunctions": None, "messageHandlers": [
        {"messageType": msg, "pageScope": True, "sessionScope": False, "viewScope": False, "script": indent(code)}]}


def dot_click(code):
    return {"dom": {"onClick": script(code)}}


# ---- Kit/TableMenuItem: one row of a menu ----
def menu_item():
    """One menu row: a tick (when set) and the text; a rule when its kind is 'rule'. Click, Enter or Space runs it."""
    code = ("system.perspective.sendMessage('kitCmd', {'key': self.view.params.key, 'op': self.view.params.op, 'field': self.view.params.field, "
            "'value': self.view.params.value}, scope='page')\nif not self.view.params.keep:\n\tsystem.perspective.closePopup('kit-menu-' + self.view.params.key)")
    events = {"dom": {"onClick": script(code), "onKeyDown": script("if event.key in ('Enter', ' '):\n" + indent(code))}}
    check = comp("ia.display.label", "Check", {"text": ""}, fix("18px"), {"props.text": expr('if({view.params.check}, "✓", "")'), "props.style.classes": expr('"kit-mi-check"')})
    name = comp("ia.display.label", "Name", {"text": ""}, grow(1), {"props.text": prop("view.params.text"), "props.style.classes": expr('"kit-mi-name"')})
    row = flex("Item", [check, name], classes="kit-mi", align="center", events=events,
               binds={"props.style.classes": expr('if({view.params.kind} = "top", "kit-mi kit-mi-top", "kit-mi")')})
    # tabindex 1, like the popup it sits in: Perspective gives the popup tabindex 1 and focuses it, so Tab goes from the popup to the first row only
    # when the rows share its tabindex (a 0 would send Tab to the top of the page)
    row["meta"]["tabIndex"] = 1
    root = flex("Cell", [row], direction="column")
    return view(root, params={"key": "kit", "text": "", "op": "", "field": "", "value": "", "check": False, "keep": False, "kind": "item"}, size=(260, 30))


def menu():
    """The popup of the three-dot column menu, the Columns chooser and the Group by list: a repeater of rows and, for a text column, a filter box."""
    rep = comp("ia.display.flex-repeater", "Items", {"path": "Kit/TableMenuItem", "direction": "column", "instances": [],
               "elementPosition": {"basis": "auto", "grow": 0, "shrink": 0}, "style": {"classes": "", "overflowX": "hidden", "overflowY": "hidden"}}, grow(1),
               {"props.instances": sb({"cols": "{view.custom.cols}", "aggs": "{view.custom.aggs}", "gsort": "{view.params.gsort}", "columns": "{view.params.columns}",
                                       "key": "{view.params.key}", "mode": "{view.params.mode}", "field": "{view.params.field}", "group": "{view.params.group}", "dense": "{view.params.dense}"},
                                      "return kittable.getMenuInstances(value)")})
    lab = comp("ia.display.label", "FilterLabel", {"text": "Filter: contains"}, fix("auto"))
    lab["props"]["style"] = {"classes": "kit-menufilter-label"}
    box = comp("ia.input.text-field", "FilterText", {"text": "", "placeholder": "Type, then Enter", "deferUpdates": False, "style": {"classes": "kit-input"}}, fix("34px"),
               {"props.text": prop("view.custom.filterText", True)},
               events={"dom": {"onKeyDown": script("if event.key == 'Enter':\n\tsystem.perspective.sendMessage('kitCmd', {'key': self.view.params.key, 'op': 'filter', 'field': self.view.params.field, "
                                                    "'value': self.view.custom.filterText}, scope='page')\n\tsystem.perspective.closePopup('kit-menu-' + self.view.params.key)")}})
    box["position"] = fix("34px")
    flt = flex("Filter", [lab, box], direction="column", classes="kit-menufilter", gap=4, pos=fix("auto"),
               binds={"props.style.display": expr('if({view.params.filtering}, "flex", "none")')})
    root = flex("Menu", [rep, flt], direction="column", classes="kit-menu", scripts=handlers("kitCmd", "kittable.runMenuCmd(self.view, payload)"),
                events={"dom": {"onKeyDown": script("if event.key == 'Escape':\n\tsystem.perspective.closePopup('kit-menu-' + self.view.params.key)")}})
    return view(root, params={"key": "kit", "mode": "col", "field": "", "columns": [], "cols": [], "aggs": {}, "gsort": {}, "group": "", "filters": {}, "filtering": False, "dense": False},
                custom={"cols": [], "aggs": {}, "filterText": ""},
                binds={"custom.cols": prop("view.params.cols"), "custom.aggs": prop("view.params.aggs"),
                       "custom.filterText": sb({"filters": "{view.params.filters}", "field": "{view.params.field}"}, "return kittable.getFilterText(value)")},
                size=(260, 400))


# ---- Kit/Table ----
def pill(name, text, code, classes_expr=None):
    c = comp("ia.display.label", name, {"text": text}, fix("auto"), {"props.style.classes": expr(classes_expr or '"kit-pill"')},
             events={"dom": {"onClick": script(code), "onKeyDown": script("if event.key in ('Enter', ' '):\n\t" + code)}}, meta={"tabIndex": 0})
    return c


def table():
    base = {"data": [], "columns": [], "enableHeader": False, "enableFooter": True, "virtualized": True, "dragOrderable": False,
            "pager": {"top": False, "bottom": False}, "filter": {"enabled": True, "text": ""}, "selection": {"mode": "multiple interval"},
            "rows": {"height": 34, "striped": {"enabled": False}, "style": {"classes": "kit-row"}},
            "cells": {"style": {"classes": "kit-cell"}}, "footerStyle": {"classes": "kit-foot"},
            "bodyStyle": {"classes": ""}, "style": {"classes": "kit-table"},
            "emptyMessage": {"noData": {"text": "No rows"}, "noFilterResults": {"text": "No rows match"}}}
    g = '{view.custom.groupBy} = ""'
    binds = {"props.columns": prop("view.custom.cols"),
             "props.rows.height": expr("if({view.custom.dense}, 26, 36)"),
             "props.enableFooter": prop("view.custom.hasTotals"),
             "props.selection.mode": expr('if(%s, "multiple interval", "none")' % g),
             "props.selection.data": prop("view.custom.sel", two=True),
             "props.filter.text": expr('if(%s, {view.custom.search}, "")' % g),
             "props.emptyMessage.noData.text": expr("{view.custom.info.empty}"),
             "props.data": sb({"ready": "{view.custom.ready}", "source": "{view.params.source}", "args": "{view.params.args}", "tick": "{view.custom.tick}",
                               "columns": "{view.params.columns}", "group": "{view.custom.groupBy}", "shape": "{view.custom.shape}",
                               "page": "{view.custom.page}", "size": "{view.params.pageSize}", "load": "{view.custom.loadId}"},
                              "return kittable.getRows(value)")}
    return comp("ia.display.table", "Rows", base, grow(1), binds, events={"component": {"onRowClick": script("kittable.rowClick(self.view, self.session, self.page, event.value, event.row, self.props.data)")}})


def head_slot(i):
    h = "view.custom.head[%d]" % i
    code = "kittable.openMenu(self.view, event, self.page, 'col', self.view.custom.head[%d]['field'], self.view.custom.menuY)" % i
    title = comp("ia.display.label", "T%d" % i, {"text": ""}, grow(1), {"props.text": prop(h + ".title"), "props.style.classes": expr('"kit-hl"')})
    mark = comp("ia.display.label", "M%d" % i, {"text": ""}, fix("auto"), {"props.text": prop(h + ".mark"), "props.style.classes": expr('if({%s.mark} = "", "kit-hm kit-hm-none", "kit-hm")' % h)})
    # a real button: Tab reaches it, Enter and Space click it, the theme draws its focus ring, and its text ("Who column menu") is its
    # accessible name. The text is hidden and three dots are drawn in its place (see .kit-dots).
    dots = comp("ia.input.button", "D%d" % i, {"text": "", "style": {"classes": "kit-dots"}}, fix("24px"),
                {"props.text": expr('{%s.title} + " column menu"' % h)}, events={"dom": {"onClick": script(code)}})
    # hidden until the display binding has run: a cell drawn before its width binding is 10 px wide and scrolls its contents (first paint)
    return flex("H%d" % i, [title, mark, dots], classes="kit-hc", align="center", pos={"grow": 0, "shrink": 0, "basis": "0px"}, style={"display": "none", "overflow": "hidden"},
                binds={"position.basis": expr('if({%s.w} > 0, {%s.w} + "px", "0px")' % (h, h)), "position.grow": prop(h + ".grow"),
                       "props.style.display": expr('if({%s.show}, "flex", "none")' % h)})


def table_view():
    info = flex("Info", [
        comp("ia.display.label", "Title", {"text": ""}, fix("auto"), {"props.text": prop("view.params.title"), "props.style.classes": expr('"kit-title"')}),
        comp("ia.display.label", "Count", {"text": ""}, fix("auto"), {"props.text": expr(
            'if({view.custom.search} = {view.custom.searchApplied}, {view.custom.info.text}, {view.custom.info.total} + " rows, Enter counts matches")'),
            "props.style.classes": expr('"kit-count"')}),
        flex("Paging", [
            pill("Newer", "Newer", "kittable.setPage(self.view, -1)", 'if({view.custom.info.newer}, "kit-pill", "kit-pill kit-pill-off")'),
            pill("Older", "Older", "kittable.setPage(self.view, 1)", 'if({view.custom.info.older}, "kit-pill", "kit-pill kit-pill-off")'),
        ], align="center", gap=8, pos=fix("auto"), style={"overflow": "hidden"}, binds={"props.style.display": expr('if({view.custom.info.paged}, "flex", "none")')}),
        flex("Selection", [
            comp("ia.display.label", "Selected", {"text": ""}, fix("auto"), {"props.text": sb({"sel": "{view.custom.sel}"}, "return kittable.getSelectionText(value['sel'])"), "props.style.classes": expr('"kit-count"')}),
            pill("Copy", "Copy", "kittable.copySelection(self.view)"),
            pill("Clear", "Clear", "self.view.custom.sel = []"),
        ], align="center", gap=8, pos=fix("auto"), style={"overflow": "hidden"}, binds={"props.style.display": expr('if(len({view.custom.sel}) > 0, "flex", "none")')}),
    ], classes="kit-info", align="center", pos=fix("30px"), style={"overflow": "hidden"})
    bar = flex("Bar", [
        comp("ia.input.text-field", "Search", {"text": "", "placeholder": "Search", "deferUpdates": False, "style": {"classes": "kit-input kit-search"}}, {"grow": 1, "shrink": 1, "basis": "230px"},
             {"props.text": prop("view.custom.search", True),
              },
             events={"dom": {"onKeyDown": script("if event.key == 'Enter':\n\tkittable.applySearch(self.view, True)")}}),
        comp("ia.display.label", "GroupLabel", {"text": "Group by"}, fix("auto"), {"props.style.classes": expr('"kit-barlabel kit-wide"')}),
        comp("ia.input.dropdown", "Group", {"options": [], "value": "__none__", "placeholder": {"text": "(none)"}, "showClearIcon": False, "search": {"enabled": False}, "style": {"classes": "kit-input kit-select kit-group"}}, fix("160px"),
             {"props.options": sb({"columns": "{view.params.columns}"}, "return kittable.getGroupOptions(value['columns'])"),
              "props.value": expr('if({view.custom.groupBy} = "", "__none__", {view.custom.groupBy})')},
             events={"component": {"onActionPerformed": script("kittable.setGroup(self.view, self.props.value)")}}),
        comp("ia.input.button", "Density", {"text": "Comfortable", "style": {"classes": "kit-btn kit-wide"}}, fix("120px"),
             {"props.text": expr('if({view.custom.dense}, "Compact", "Comfortable")')},
             events={"component": {"onActionPerformed": script("self.view.custom.dense = not self.view.custom.dense")}}),
        flex("Grouped", [
            pill("Chip", "", "kittable.setGroup(self.view, u'')", '"kit-pill kit-pill-on"'),
            pill("ExpandAll", "Expand all", "kittable.setAllGroups(self.view, False)", '"kit-pill kit-wide"'),
            pill("CollapseAll", "Collapse all", "kittable.setAllGroups(self.view, True)", '"kit-pill kit-wide"'),
        ], align="center", gap=8, pos=fix("auto"), style={"overflow": "hidden"}, binds={"props.style.display": expr('if({view.custom.groupBy} != "", "flex", "none")')}),
        comp("ia.input.button", "More", {"text": "More", "style": {"classes": "kit-dots kit-more"}}, fix("28px"),
             events={"dom": {"onClick": script("kittable.openMenu(self.view, event, self.page, 'more', u'', self.view.custom.menuY)")}}),
        comp("ia.input.button", "Columns", {"text": "Columns", "style": {"classes": "kit-btn kit-wide"}}, fix("120px"),
             events={"dom": {"onClick": script("kittable.openMenu(self.view, event, self.page, 'cols', u'', self.view.custom.menuY)")}}),
        comp("ia.input.button", "Export", {"text": "Export", "style": {"classes": "kit-btn kit-wide"}}, fix("120px"),
             events={"component": {"onActionPerformed": script("kittable.exportCsv(self.view)")}}),
    ], classes="kit-bar", align="center", pos=fix("42px"), style={"overflow": "hidden"})
    # the Grouped chip's text names the column; set from the group
    grp = [c for c in bar["children"] if c["meta"]["name"] == "Grouped"][0]
    grp["children"][0]["propConfig"]["props.text"] = {"binding": sb({"group": "{view.custom.groupBy}", "columns": "{view.params.columns}"}, "return kittable.getGroupChip(value)")}

    # Perspective writes overflow: auto inline on a flex container that sets none, and an inline style beats the stylesheet: with classic
    # scroll bars a box a fraction of a pixel too small drew a second bar beside the Table's. Every container here says overflow hidden.
    head = flex("HeadRow", [head_slot(i) for i in range(16)], classes="kit-headrow", align="stretch", pos=fix("34px"), style={"overflow": "hidden"})
    root = flex("Root", [info, bar, head, flex("Body", [table()], direction="column", pos=grow(1), style={"minHeight": "0px", "overflow": "hidden"})],
                direction="column", classes="kit-root", style={"overflow": "hidden"},
                binds={"props.style.classes": expr('"kit-root" + if({view.custom.dense}, " kit-dense", "") + if({view.custom.hasTotals}, " kit-totals", "")')},
                scripts=handlers("kitCmd", "kittable.runCmd(self.view, payload)"))
    slot = {"show": False, "w": 0, "grow": 0, "title": "", "field": "", "mark": "", "num": False}
    custom = {"ready": False, "search": "", "searchApplied": "", "dense": False, "groupBy": "", "collapsed": [], "shape": {}, "page": 0, "loadId": 0, "sel": [],
              "aggs": {}, "filters": {}, "gsort": {"on": False, "field": "", "dir": "none"}, "hasTotals": False, "tick": 0, "menuY": 0, "layoutSig": "", "cols": [], "head": [dict(slot) for _ in range(16)],
              "info": {"total": 0, "shown": 0, "text": "", "empty": "Loading…", "error": False, "paged": False, "newer": False, "older": False}}
    binds = {"custom.info": sb({"ready": "{view.custom.ready}", "source": "{view.params.source}", "args": "{view.params.args}", "tick": "{view.custom.tick}",
                                "columns": "{view.params.columns}", "search": "{view.custom.searchApplied}", "page": "{view.custom.page}", "size": "{view.params.pageSize}",
                                "countSource": "{view.params.countSource}", "load": "{view.custom.loadId}", "filters": "{view.custom.filters}"}, "return kittable.getInfo(value)"),
             "custom.head": sb({"cols": "{view.custom.cols}", "columns": "{view.params.columns}", "gsort": "{view.custom.gsort}", "filters": "{view.custom.filters}"},
                               "return kittable.getHeadSlots(value)")}
    v = view(root, params={"source": "", "args": [], "tick": 0, "columns": [], "title": "", "key": "kit", "pageSize": 0, "countSource": "", "defaultGroup": "", "rowAction": ""},
             custom=custom, binds=binds, startup="kittable.startView(self.view, self.session)")
    v["propConfig"]["params.tick"]["onChange"] = {"enabled": True, "script": indent("# the host delivers its tick again after load; only a changed value may reach the data and count bindings\nif self.view.custom.tick != self.view.params.tick:\n\tself.view.custom.tick = self.view.params.tick")}
    v["propConfig"]["custom.cols"] = {"onChange": {"enabled": True, "script": indent("kittable.saveView(self.view, self.session, self.view.custom.layoutSig)")}}
    # density on its own is saved too: before, it was kept only when a column change followed
    v["propConfig"]["custom.dense"] = {"onChange": {"enabled": True, "script": indent("kittable.saveView(self.view, self.session, self.view.custom.layoutSig)")}}
    v["propConfig"]["custom.search"] = {"onChange": {"enabled": True, "script": indent("kittable.applySearch(self.view, False)")}}
    return v


def main(project):
    views = os.path.join(project, "com.inductiveautomation.perspective", "views", "Kit")
    if not os.path.isdir(os.path.join(project, "com.inductiveautomation.perspective")):
        sys.exit("%s is not a project folder (no com.inductiveautomation.perspective in it)" % project)
    for name, v in (("Table", table_view()), ("TableMenu", menu()), ("TableMenuItem", menu_item())):
        d = os.path.join(views, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "view.json"), "w") as f:
            json.dump(v, f, indent=2)
            f.write("\n")
    print("wrote the table kit's 3 views under %s" % views)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 tools/kit_table.py <project folder>   (build_views.py calls main() with the repo root)")
    main(sys.argv[1])
