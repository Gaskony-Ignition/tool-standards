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


def start():
	"""Start a background task."""
	class Task(Runnable):
		def run(self):
			pass

		def start(self):
			pass
	return Task()


def make():
	"""Return a counter."""
	def make():
		return 1
	return make


class Holder(object):
	"""Holds a callback."""

	class Inner(object):
		def go(self):
			pass
