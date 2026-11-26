"""Stand-in kit generator for the K1 fixtures: writes three tiny views."""
import json
import os


def main(project):
    for name in ("Table", "TableMenu", "TableMenuItem"):
        d = os.path.join(project, "com.inductiveautomation.perspective", "views", "Kit", name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "view.json"), "w") as f:
            json.dump({"root": {"type": "ia.container.flex", "meta": {"name": name}}}, f)
