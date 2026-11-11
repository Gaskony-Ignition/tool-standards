EVENTS_TABLE = "app_events"


def legacy():
	"""Python 2 only, so the constant cannot be proved and the regex decides."""
	print "counting"
	return system.db.runScalarQuery("SELECT COUNT(*) FROM %s" % EVENTS_TABLE)
