def save(component):
	"""Remember the area the view was opened for; return the filter count."""
	view = component.view
	# view.params.note is shown in the header.
	view.custom["last"] = view.params.areaId
	return view.custom.filter.a + len(view.custom.mode)


def shift(component):
	"""Return the shift the view was opened for."""
	return component.view.params.shiftId
