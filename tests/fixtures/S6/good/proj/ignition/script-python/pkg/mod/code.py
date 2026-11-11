def f(i, sql, db):
	"""F."""
	system.db.runPrepQuery("SELECT * FROM t WHERE id=" + "?", [i])
	system.db.runQuery("SELECT 100 % 7")
	system.db.runQuery(sql, db + "_ro")
	return system.db.runNamedQuery("q", {})
