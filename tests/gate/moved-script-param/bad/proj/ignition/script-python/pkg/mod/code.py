def save(component, event):
	"""Write the area the view was opened for."""
	system.tag.writeBlocking(["[default]Selected"], [component.view.params.areaId])
