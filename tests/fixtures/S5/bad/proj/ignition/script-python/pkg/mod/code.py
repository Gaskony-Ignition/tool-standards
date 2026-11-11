import system
from system.tag import readBlocking


def f():
	"""F."""
	try:
		print "hi"
	except ValueError, e:
		print(e)
