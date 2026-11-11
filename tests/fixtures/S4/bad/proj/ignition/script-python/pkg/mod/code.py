def f():
	"""Read the tag; previously this polled."""
	# we decided to cache this
	# result = system.tag.readBlocking(paths)
	return 1


# FIX-12 moved here


def g():
	"""Read the rows."""
	# This used to return a list.
	# The zero-fill used to be a join.
	# We never used to check the quality.
	return 1


def h():
	"""Correct at the time of writing."""
	# This file used to carry the keys.
	return 1
