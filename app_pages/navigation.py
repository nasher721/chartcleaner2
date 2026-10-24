NAV_GROUPS = (
    ("Work", (
        ("/", "cleaning_services", "Clean"),
        ("/batch", "layers", "Batch"),
        ("/census", "bed", "Census"),
    )),
    ("Customize", (
        ("/rules", "highlight", "My text rules"),
        ("/pipeline", "tune", "Pipeline & Rules"),
        ("/scripts", "code", "Custom Scripts"),
    )),
    ("System", (
        ("/stats", "insights", "Statistics"),
        ("/settings", "settings", "Settings"),
        ("/doctor", "medical_services", "Doctor"),
    )),
)
NAV = tuple(item for _name, items in NAV_GROUPS for item in items)
SECTION_NUMBERS = {
    "clean": ("01", "Clean"), "batch": ("02", "Batch"),
    "census": ("03", "Census"), "rules": ("04", "My text rules"),
    "pipeline": ("05", "Pipeline & Rules"), "stats": ("06", "Statistics"),
    "scripts": ("07", "Custom Scripts"), "settings": ("08", "Settings"),
    "doctor": ("09", "Doctor"),
}


def group_for(active: str) -> str:
    slug = (active or "clean").strip("/") or "clean"
    path = "/" if slug == "clean" else f"/{slug}"
    for name, items in NAV_GROUPS:
        if any(item_path == path for item_path, _icon, _label in items):
            return name
    return ""
