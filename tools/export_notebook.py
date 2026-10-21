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

"""Export notebooks/gatekeeper_triage.ipynb, with its outputs, to notebooks/gatekeeper_triage.html.

Dan's ruling 39 Part 3.5. nbconvert's classic template, then the one image its stylesheet embeds (the code
editor's tab marker, a base64 PNG) is replaced by `none`: the repository's secret scan reads any long base64 run
as a key. Nothing else in the page changes. Run from the repository root with the `[notebook]` extra installed:

    python tools/export_notebook.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NOTEBOOK = REPO / "notebooks" / "gatekeeper_triage.ipynb"
EMBEDDED = re.compile(r"url\(data:image/[a-z+.-]+;base64,[A-Za-z0-9+/=]+\)")


def clean(html: str) -> tuple[str, int]:
    """The page with every image embedded in its stylesheet replaced by `none`, and how many there were."""
    return EMBEDDED.subn("none", html)


def main() -> int:
    subprocess.run([sys.executable, "-m", "nbconvert", "--to", "html", "--template", "classic", str(NOTEBOOK),
                    "--output", NOTEBOOK.stem], check=True, cwd=REPO)
    page = NOTEBOOK.with_suffix(".html")
    html, removed = clean(page.read_text(encoding="utf-8"))
    if "base64," in html:
        raise SystemExit("export_notebook: an embedded base64 payload is still in the page")
    page.write_bytes(html.encode("utf-8"))
    print(f"wrote {page.relative_to(REPO)}; {removed} stylesheet image(s) replaced by none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
