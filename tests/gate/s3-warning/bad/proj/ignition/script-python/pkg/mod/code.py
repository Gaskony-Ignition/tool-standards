"""Totals.

One.
Two.
Three.
Four.
"""
import system

# ===== Logging =====
LOG = system.util.getLogger("pkg.mod")


# The rows come from the historian, which returns
# one row per tag per interval, so a sum over them
# counts each tag once per interval: callers divide
# by the interval count themselves.
def total(rows):
	"""Return the sum of rows."""
	return sum(rows)
