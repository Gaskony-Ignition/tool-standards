EVENTS_TABLE = "app_events"
TABLES = {"builds": "app_builds", "events": "app_events"}
COLUMNS = ("id", "name")


def count():
	"""Rows in the events table."""
	return system.db.runScalarQuery("SELECT COUNT(*) FROM %s" % EVENTS_TABLE, "db")


def countOf(key):
	"""Rows in one named table."""
	return system.db.runScalarQuery("SELECT COUNT(*) FROM %s" % TABLES[key], "db")


def wipe(key, i):
	"""Delete one row; the table is a constant and the id a parameter."""
	system.db.runPrepUpdate("DELETE FROM %s WHERE id=?" % TABLES[key], [i])
	system.db.runUpdateQuery("DELETE FROM " + EVENTS_TABLE + " WHERE id=0")
	return system.db.runQuery("SELECT {} FROM {}".format(COLUMNS[0], EVENTS_TABLE))
