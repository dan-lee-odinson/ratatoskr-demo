"""Ruling 39 Part 3.5: tools/export_notebook.py takes the stylesheet's embedded image out of the HTML export and
nothing else, so the repository's secret scan passes on the committed page."""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("export_notebook", REPO / "tools" / "export_notebook.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def test_clean_replaces_only_the_stylesheet_images():
    page = ("<style>.tab { background: url(data:image/png;base64," + "iVBORw0KGgo" * 8 + "); }</style>"
            "<p>Mode: LIVE, url(not-data)</p>")
    cleaned, removed = export.clean(page)
    assert removed == 1 and "base64," not in cleaned
    assert cleaned == "<style>.tab { background: none; }</style><p>Mode: LIVE, url(not-data)</p>"


def test_the_committed_export_has_no_embedded_payload_and_shows_a_live_run():
    html = (REPO / "notebooks" / "gatekeeper_triage.html").read_text(encoding="utf-8")
    assert "base64," not in html and "Mode: LIVE" in html and "9. The fresh evaluation" in html
