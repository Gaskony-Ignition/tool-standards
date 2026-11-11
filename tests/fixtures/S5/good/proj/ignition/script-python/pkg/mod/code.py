from systemutil import x


def f(logger):
	"""F."""
	logger.info("print this")
	printer = 1
	logger.print_ok = printer
	try:
		return 1
	except (ValueError, TypeError):
		return 0
