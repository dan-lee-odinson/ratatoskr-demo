# Copyright 2026 Dan Lee-Odinson
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The companion app prototype's recording (Dan's ruling 61, addendum): the brief's section 8 steps, performed in the
installed Chrome at 390 x 844 by Playwright, recorded, and converted to H.264 MP4 at 30 fps.

    python app/demo/record.py --out recording.mp4          against the fixtures, served on 127.0.0.1 by this script
    python app/demo/record.py --live --out recording.mp4   against the courier's owner service (--loop --owner-service)

Needs `pip install playwright` (no browser download: the installed Chrome, channel "chrome") and an ffmpeg with
libx264: --ffmpeg, else the imageio-ffmpeg wheel's, else ShareX's, else one on PATH. A pause of two to three seconds
follows each action, and a drawn cursor follows the mouse (a recording shows no pointer of its own).

The context records with record_video_dir when Playwright's own ffmpeg is installed (`playwright install ffmpeg`, a
download). Without it, the same page is recorded through Chrome's screencast (the DevTools protocol, as Playwright's
recorder itself does) and the frames are timed into the MP4 by the ffmpeg above: no download either way.

With --live the token is read from the courier's settings, as the courier reads it, and handed to the page in the
address's fragment, which the page keeps and takes out of the address; it is never printed. The live run makes no
external write: the task the live check filed (the newest answered via the app, as GET /owner/pending reports it) is
shown as the ledger has it (acknowledged), and a tag stays in the page.
"""

from __future__ import annotations

import argparse
import base64
import functools
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
SIZE = {"width": 390, "height": 844}
SHAREX_FFMPEG = Path(r"C:\Program Files\ShareX\ffmpeg.exe")
CURSOR = """
window.addEventListener("DOMContentLoaded", () => {
  const c = document.createElement("div");
  c.style.cssText = "position:fixed;left:-40px;top:-40px;width:22px;height:22px;margin:-11px 0 0 -11px;" +
    "border-radius:50%;background:rgba(162,85,44,.30);border:2px solid rgba(162,85,44,.95);z-index:2147483647;" +
    "pointer-events:none;transition:transform .12s";
  document.documentElement.appendChild(c);
  addEventListener("mousemove", e => { c.style.left = e.clientX + "px"; c.style.top = e.clientY + "px"; }, true);
  addEventListener("mousedown", () => { c.style.transform = "scale(.6)"; }, true);
  addEventListener("mouseup", () => { c.style.transform = ""; }, true);
});
"""


class Director:
    """Every action as a viewer would see it: the cursor travels to the element, clicks, and the page rests."""

    def __init__(self, page, pause: float = 2.5) -> None:
        self.page, self.pause = page, pause

    def rest(self, seconds: float | None = None) -> None:
        self.page.wait_for_timeout(int(1000 * (self.pause if seconds is None else seconds)))

    def show(self, locator) -> tuple[float, float]:
        """Scroll (in a few visible steps) until the element is in view; its centre."""
        locator.wait_for(state="visible")
        for _ in range(12):
            box = locator.bounding_box()
            if 90 <= box["y"] and box["y"] + box["height"] <= SIZE["height"] - 80:
                break
            self.page.mouse.wheel(0, max(-260, min(260, box["y"] - SIZE["height"] / 3)))
            self.page.wait_for_timeout(160)
        box = locator.bounding_box()
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def click(self, locator, rest: float | None = None) -> None:
        x, y = self.show(locator)
        self.page.mouse.move(x, y, steps=22)
        self.page.wait_for_timeout(350)
        self.page.mouse.click(x, y)
        self.rest(rest)

    def type(self, locator, text: str, rest: float | None = None) -> None:
        self.click(locator, rest=0.4)
        self.page.keyboard.type(text, delay=140)
        self.rest(rest)

    def choose(self, locator, value: str, rest: float | None = None) -> None:
        x, y = self.show(locator)
        self.page.mouse.move(x, y, steps=22)
        self.page.wait_for_timeout(350)
        locator.select_option(value)
        self.rest(rest)


def fixtures_walk(d: Director, page) -> None:
    """Section 8, steps 1 to 4, against the fixtures."""
    d.rest(3)  # 1. the inbox: ID-only review requests
    d.click(page.locator("a[data-review='hold-s6']"), rest=3)  # the simulated private viewer (an unsure photo)
    d.click(page.locator(".viewer [data-act=keep-held]"), rest=2.5)  # 2. kept held, back in the inbox
    d.click(page.locator("a[data-review='hold-rec_014']"), rest=3)  # a transcript is supported too
    d.click(page.locator(".viewer [data-close-viewer]"), rest=2)
    d.click(page.locator("[data-open='task-rec_009-3']"), rest=3)  # 3. Decider unsure: work 0.68, writing 0.23
    d.type(page.locator("[data-tag-input]"), "reading", rest=0.8)
    d.click(page.locator(".sheet [data-add-tag]"), rest=1.5)
    d.click(page.locator(".sheet input[name=category][value=writing]"), rest=1.5)
    d.click(page.locator(".sheet button[type=submit]"), rest=3)  # Categorized by owner
    d.click(page.locator(".sheet [data-close]"), rest=2)
    d.click(page.locator("[data-tab=tasks]"), rest=2.5)  # 4. the grouped view
    d.show(page.locator("[data-group=writing]"))
    d.rest(2.5)
    d.choose(page.locator("select[data-filter=tag]"), "reading", rest=3)
    d.click(page.locator("[data-tab=activity]"), rest=3.5)  # the owner-action history: Model and Owner lines


def filed_via_app(base: str, token: str) -> str:
    """The task the live check filed: the newest one answered via the app, as the owner service reports it."""
    request = urllib.request.Request(f"{base}/owner/pending", headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback, never through a proxy
    with opener.open(request, timeout=10) as answer:
        items = json.load(answer)["items"]
    filed = [item for item in items if (item.get("resolved") or {}).get("via") == "app"]
    if not filed:
        raise SystemExit("record --live: no task answered via the app yet (the live check files one first)")
    return max(filed, key=lambda item: item["resolved"].get("at") or "")["id"]


def live_walk(d: Director, page, filed: str) -> None:
    """Section 8 against the owner service: a live hold if the ledger has one, then the task the live check filed,
    acknowledged; nothing is written to Todoist here."""
    d.rest(3)
    hold = page.locator("a[data-review^='hold-']").first
    if hold.count():
        d.click(hold, rest=3)  # a live hold: its content stays on the laptop
        d.click(page.locator(".viewer [data-act=keep-held]"), rest=2.5)
    d.click(page.locator("[data-tab=tasks]"), rest=2.5)
    card = page.locator(f".card.task[data-card='{filed}']")
    d.click(card.locator("[data-open]"), rest=3.5)  # its sheet: Decider's answer, the owner's, acknowledged
    d.type(page.locator("[data-tag-input]"), "reading", rest=0.8)
    d.click(page.locator(".sheet [data-add-tag]"), rest=1.5)
    d.click(page.locator(".sheet button[type=submit]"), rest=3)  # the tag: saved in the app, not in Todoist
    d.click(page.locator(".sheet [data-close]"), rest=2)
    d.click(page.locator("[data-tab=activity]"), rest=3.5)


class Screencast:
    """Chrome's screencast of one page: a JPEG each time the page changes, with its time; nothing downloaded."""

    def __init__(self, page, folder: Path) -> None:
        self.folder, self.frames = folder, []
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._frame)
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": SIZE["width"],
                                               "maxHeight": SIZE["height"], "everyNthFrame": 1})

    def _frame(self, params: dict) -> None:
        path = self.folder / f"{len(self.frames):05d}.jpg"
        path.write_bytes(base64.b64decode(params["data"]))
        self.frames.append((float(params["metadata"]["timestamp"]), path))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})

    def stop(self) -> Path:
        """The frames as an ffconcat list, each shown until the next, the last until now."""
        end = time.time()
        self.cdp.send("Page.stopScreencast")
        listing = ["ffconcat version 1.0"]
        for (ts, path), following in zip(self.frames, self.frames[1:] + [(end, None)]):
            listing += [f"file '{path.name}'", f"duration {max(following[0] - ts, 0.001):.4f}"]
        listing.append(f"file '{self.frames[-1][1].name}'")
        concat = self.folder / "frames.ffconcat"
        concat.write_text("\n".join(listing) + "\n", encoding="utf-8")
        return concat


def ffmpeg(given: str | None) -> str:
    if given:
        return given
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    if SHAREX_FFMPEG.is_file():
        return str(SHAREX_FFMPEG)
    found = shutil.which("ffmpeg")
    if not found:
        raise SystemExit("record: no ffmpeg (pip install imageio-ffmpeg, or --ffmpeg PATH)")
    return found


def convert(tool: str, source: Path, out: Path) -> float:
    """H.264 MP4 at 30 fps, from Playwright's WebM or the screencast's frame list; its running time in seconds."""
    given = ["-f", "concat", "-safe", "0", "-i", str(source)] if source.suffix == ".ffconcat" else ["-i", str(source)]
    subprocess.run([tool, "-y", "-loglevel", "error", *given, "-r", "30", "-vf", "scale=out_range=tv,format=yuv420p",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-color_range", "tv", "-movflags", "+faststart",
                    str(out)], check=True)  # JPEG frames are full range: the MP4 is standard (tv) range yuv420p
    probe = subprocess.run([tool, "-hide_banner", "-i", str(out)], capture_output=True, text=True)
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe.stderr).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def serve_fixtures() -> tuple[ThreadingHTTPServer, str]:
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(APP)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/index.html"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True, help="the MP4 to write")
    parser.add_argument("--live", action="store_true", help="against the courier's owner service, not the fixtures")
    parser.add_argument("--ffmpeg", help="an ffmpeg with libx264")
    args = parser.parse_args(argv)
    from playwright.sync_api import sync_playwright

    server = None
    if args.live:
        sys.path.insert(0, str(REPO))
        from ratatoskr_demo.config import load_settings

        settings = load_settings()
        token = settings.secrets.owner_service_token
        if not token:
            raise SystemExit("record --live: no OWNER_SERVICE_TOKEN in the courier's settings")
        base = f"http://127.0.0.1:{settings.owner.port}"
        filed = filed_via_app(base, token)
        url = f"{base}/#token={token}"
    else:
        server, url = serve_fixtures()
    out = Path(args.out).resolve()
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        screencast = None
        try:
            context = browser.new_context(viewport=SIZE, record_video_dir=tmp, record_video_size=SIZE)
            page = context.new_page()
        except Exception as exc:  # Playwright's own ffmpeg is not installed: Chrome's screencast instead
            if "ffmpeg" not in str(exc):
                raise
            print("record: Playwright's ffmpeg is not installed; recording through Chrome's screencast", flush=True)
            context = browser.new_context(viewport=SIZE)
            page = context.new_page()
            screencast = Screencast(page, Path(tmp))
        context.add_init_script(CURSOR)
        page.goto(url)
        if args.live:
            del token, url
        page.wait_for_selector(".tabs")
        if args.live:
            live_walk(Director(page), page, filed)
        else:
            fixtures_walk(Director(page), page)
        source = screencast.stop() if screencast else None
        video = None if screencast else page.video
        context.close()
        browser.close()
        seconds = convert(ffmpeg(args.ffmpeg), source or Path(video.path()), out)
    if server is not None:
        server.shutdown()
    print(f"wrote {out} ({seconds:.1f} s, {SIZE['width']}x{SIZE['height']}, H.264, 30 fps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
