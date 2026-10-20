# Fixtures

Synthetic Pocket API responses shaped to Pocket's **documented** schema. Every person,
organisation and conversation here is invented. "Vargrheim" is a fictional client name: it is the
shipped stop-list term, so any recording that names it is held before any model sees it.

**ASSUMPTION, flagged:** the populated shape of `transcript` is undocumented. These fixtures use a
plain string. `transcript.extract_text()` also accepts the object and segment-list shapes, and
`ratatoskr-demo --inspect-shape <recording_id>` confirms the shape on a real recording (key names
and types only).

| file | what it exercises |
|---|---|
| `list_recordings_page1.json` | list response with all five recordings, `has_more: false` |
| `rec_001_networking.json` | normal networking conversation → tasks |
| `rec_002_vargrheim_intro.json` | contains the word "Vargrheim" in an introduction → HOLD |
| `rec_003_keep_tagged.json` | tagged `keep` → skip forever, never scrubbable |
| `rec_004_not_ready.json` | `transcript: null`, no error → NotReady |
| `rec_005_no_followups.json` | small talk, no commitments → "Met X — no follow-ups" task |

Each `rec_*.json` is the body of `GET /public/recordings/{id}`. Beside `data`, a file may carry an
`expected` block (canonical tasks and the answers they should get, see
`ratatoskr_demo/expected.py`); the Pocket client never reads it. The bench's further recordings
are in `bench/fixtures/`.

The T-LEAK sentinel to plant is the string `ZQX-SENTINEL-7731`; the tests insert it at runtime. Do
not put it in these files.
