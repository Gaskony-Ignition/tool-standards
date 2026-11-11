EVENTS_TABLE = "app_events"
TABLES = {"builds": "app_builds", "events": "app_events"}
MIXED = ["app_builds", 7]
MOVED = "app_moved"
MOVED = "app_old"


def mixed(i):
	"""A constant table and an argument."""
	return system.db.runQuery("SELECT * FROM %s WHERE id=%s" % (EVENTS_TABLE, i))


def attribute(cfg):
	"""An attribute is not a module constant."""
	return system.db.runUpdateQuery("DELETE FROM " + cfg.TABLE)


def called():
	"""A call is not a module constant."""
	return system.db.runScalarQuery("SELECT COUNT(*) FROM {}".format(str(EVENTS_TABLE)))


def notStrings(k):
	"""A list holding a number is not a table of names."""
	return system.db.runQuery("SELECT * FROM %s" % MIXED[k])


def reassigned():
	"""A name bound twice is not a constant."""
	return system.db.runScalarQuery("SELECT COUNT(*) FROM %s" % MOVED)


def shadowed(EVENTS_TABLE):
	"""An argument that shadows the constant is input."""
	return system.db.runUpdateQuery("DELETE FROM %s" % EVENTS_TABLE)


def looped():
	"""A loop variable over the table names is not a module constant."""
	for name in sorted(TABLES):
		system.db.runQuery("SELECT 1 FROM %s" % name)
