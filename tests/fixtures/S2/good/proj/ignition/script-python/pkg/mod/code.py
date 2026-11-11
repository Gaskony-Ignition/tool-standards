def scale(x, k):
	"""Scale x by k.

	Args:
		x: the value
	"""
	return x * k


def _helper():
	return 1


class Pump(object):
	"""A pump."""

	def __init__(self):
		self.on = False

	def start(self):
		"""Start it."""
		def inner():
			return 1
		return inner()
