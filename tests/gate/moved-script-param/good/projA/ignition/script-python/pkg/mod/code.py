def save(component):
	"""Remember the area the view was opened for; return the filter count."""
	view = component.view
	view.custom["last"] = view.params.areaId
	return view.custom.filter.a


def shift(component):
	"""Return the shift the view was opened for."""
	return component.view.params.shiftId
