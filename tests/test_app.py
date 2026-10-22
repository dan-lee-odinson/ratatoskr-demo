"""Dan's ruling 61, P0: the companion app prototype in app/.

The fixtures regenerate from the committed exports byte for byte, and every model number in them is Decider's answer as
its export recorded it; a hold carries no content outside its simulated viewer; the page loads nothing from the
network; and the page's own tests (app/tests.html: the adapter contract, each state's actions, idempotent Save, the
scenarios A, B and C with their Activity entries) pass in a headless browser, Chrome or Edge, where one is installed.
"""

import html
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app"
spec = importlib.util.spec_from_file_location("app_fixtures", REPO / "tools" / "app_fixtures.py")
app_fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_fixtures)
BROWSERS = (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "google-chrome", "chromium", "chromium-browser", "microsoft-edge")


def fixtures():
    text = (APP / "fixtures.js").read_text(encoding="utf-8")
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def test_the_fixtures_regenerate_from_the_committed_exports_byte_for_byte():
    assert (APP / "fixtures.js").read_text(encoding="utf-8") == app_fixtures.render(app_fixtures.build())
    for name in app_fixtures.pictures():
        assert (APP / "fixtures" / name).read_bytes() == (REPO / "bench" / "images" / name).read_bytes()


def test_every_model_number_in_the_fixtures_is_the_answer_its_export_recorded():
    final = {r["file"]: r for r in json.loads((REPO / app_fixtures.FINAL).read_text(encoding="utf-8"))["images"]}
    cpu = json.loads((REPO / app_fixtures.VARIANTS).read_text(encoding="utf-8"))["rows"]["cpu"]
    items = fixtures()["items"]
    assert {i["kind"] for i in items} == {"hold", "scene", "task"} and not any(i["illustrative"] for i in items)
    for item in items:
        assert (REPO / item["source"]["export"]).is_file()
        if item["kind"] == "hold" and item["input"] == "photo":
            first = next(v for v in final[item["source"]["item"]]["visits"] if v["status"] == "hold")
            assert item["p"] == first["picture_p"][0] and first["reason"] == f"image-{item['verdict']}"
        elif item["kind"] == "hold":
            gate = next(g for g in cpu["gate"] if g["recording"] == item["ref"])
            assert item["p"] == max(w["p"] for w in gate["windows"]) and item["verdict"] == "sensitive"
        elif item["kind"] == "scene":
            img = final[item["source"]["item"]]["image"]
            assert (item["scene"]["choice"], item["scene"]["confidence"], item["scene"]["options"]) == (
                img["scene_choice"], img["scene_p"], img["scene_options"]) and img["scene"] == "unsure"
        else:
            rid, n = item["ref"].split(" #")
            row = next(t for t in cpu["tasks"] if t["recording"] == rid and t["n"] == int(n))
            assert item["project"]["probabilities"] == row["answers"]["project"]["probabilities"]
            assert item["project"]["confidence"] == row["judged"]["project"]["confidence"]
            assert item["project"]["sure"] == (row["judged"]["project"]["confidence"] >= item["project"]["floor"])
    tasks = [i for i in items if i["kind"] == "task"]
    assert sum(not t["project"]["sure"] for t in tasks) >= 3  # scenario C needs three uncertain tasks
    assert not any(t["ref"].startswith(("rec_014", "rec_015")) for t in tasks)  # held recordings yield no task


def test_a_hold_carries_its_content_only_into_the_simulated_viewer_and_its_id_is_a_safe_one():
    for item in fixtures()["items"]:
        if item["kind"] in ("hold", "scene"):
            assert "text" not in item and "title" not in item  # the card reads neither: ID only
            assert re.fullmatch(r"photo-[0-9a-f]{12}|rec_\d{3}", item["ref"])


def test_the_page_loads_nothing_from_the_network_and_calls_only_the_owner_service_it_came_from():
    for path in APP.iterdir():
        if path.suffix in (".html", ".js", ".css") and path.name != "tests.html":
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"https?://|//cdn|@import|XMLHttpRequest|WebSocket", text), path.name
            assert "fetch(" not in text or path.name == "adapter.js", path.name
    adapter = (APP / "adapter.js").read_text(encoding="utf-8")
    assert adapter.count("fetch(") == 1 and "ServiceAdapter(location.origin, token" in adapter  # its own origin only


def browser():
    for name in BROWSERS:
        found = shutil.which(name) or (name if Path(name).is_file() else None)
        if found:
            return found
    return None


@pytest.mark.skipif(browser() is None, reason="no Chrome or Edge to run the page's tests headless")
def test_the_pages_own_tests_pass_in_a_headless_browser(tmp_path):
    done = subprocess.run([browser(), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                           f"--user-data-dir={tmp_path}", "--allow-file-access-from-files",
                           "--virtual-time-budget=30000", "--dump-dom", (APP / "tests.html").as_uri()],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    found = re.search(r'<pre id="results">(.*?)</pre>', done.stdout, re.S)
    assert found, done.stderr[-2000:]
    results = json.loads(html.unescape(found.group(1)))
    assert results["failed"] == [] and len(results["passed"]) >= 15, results["failed"]
