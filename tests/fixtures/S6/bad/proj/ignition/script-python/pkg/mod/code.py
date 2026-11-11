def f(i, t):
	"""F."""
	system.db.runQuery("SELECT * FROM t WHERE id=" + str(i))
	return system.db.runScalarQuery(
		"SELECT COUNT(*) FROM %s" % t, "db")
