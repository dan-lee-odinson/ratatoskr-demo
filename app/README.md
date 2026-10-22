# Companion app prototype

A **mobile web prototype** of the owner's interface to Ratatoskr (Dan's ruling 61): one page of plain HTML, CSS and
JavaScript, no framework and no external asset. It is not a native app. It shows what the owner would see instead of
Telegram: ID-only review requests for held photographs and transcripts, a simulated private viewer, category and tag
choices for ordinary tasks, and a history that keeps Decider's answer apart from the owner's choice.

## Open it

Open `app/index.html` in a browser at phone width (in Chrome's device toolbar, for instance), or serve the folder on
the loopback address and open it there:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory app
```

Then `http://127.0.0.1:8765/`. Nothing in the page reaches the network. The owner's choices are kept in the browser's
local storage, so they survive navigation, a refresh and a restart; About → "Clear prototype data" starts over.

## Connect it to the courier (one real connection)

The courier can serve the page itself, with the live ledger behind it:

```bash
ratatoskr-demo --loop --owner-service
```

The owner service listens on `127.0.0.1` only, on port 8788 (an `[owner] port` in `config.toml` changes it; the
frozen evaluation configuration does not need one); an Android emulator reaches
it at `http://10.0.2.2:8788`. Its two routes need the bearer token set as `OWNER_SERVICE_TOKEN` in `.env` (any long
random string); the page asks for it once, in About, and keeps it in the browser:

- `GET /owner/pending`: the unsorted tasks from the ledger, each with the owner's answer once given; a task's text and
  Decider's probabilities only where this courier sent them to Telegram (kept in memory, never in the ledger); every
  hold ID only.
- `POST /owner/pick`: the owner's category, through the same handler as the Telegram buttons (ruling 43): the owner's
  first answer stands, from either channel; the Todoist task's `proj:` label is updated with the same command id a
  tap would send; the ledger records `project_by: owner, via: app`.

A category change shows as **pending** until the courier answers, **acknowledged** once it has updated the Todoist
task, and **failed** (with the reason) when it has not. Tags, Keep held, Release and scene answers stay in the page,
as without the service.

## The three views

- **Needs review**: a held photograph or transcript, ID only (its input type, its safe identifier, the gate and its
  verdict), with **Review privately**; a cleared task whose category Decider was unsure of, with **Categorize**; a
  photograph whose scene Decider was unsure of, with **Commitment** and **Reference**. Resolved items move to
  "Reviewed", from which they can be reopened.
- **Tasks**: the cleared tasks, grouped by category, with their due date, source and tags, and filters by category
  and by tag. A task's sheet lists every category with Decider's probability, best first, and **Leave unsorted**.
- **Activity**: each owner action, with Decider's recorded answer kept apart from the owner's choice, and its status.

## The courier's rules, as the page applies them

- A **sensitive** hold has no release; an **unsure** hold has **Release (owner authorization)**, which resumes the
  remaining checks for that photo and is not a model clear or a permission to upload.
- Privacy, scene and category uncertainty are three states with different actions.
- A category is the task's `proj:` label in Todoist; the owner's first category answer stands, as with the
  Telegram buttons. Tags are the app's own.
- A repeated **Save** records nothing more.

## Real and simulated

| Part | In this prototype |
|---|---|
| Decider's answers (probabilities, confidences, floors) | Real: as the committed exports recorded them; each item names its export |
| The photographs and the transcript in the viewer | Real fixtures: the repository's fictional test content |
| The rules above | Real: the courier's |
| The secure link and the private viewer | Simulated: the link is intercepted inside the page and never followed; no connection is made |
| Keep held, Release, scene answers | Prototype only: recorded in the page; the courier is not told |
| Category changes | Without the service: saved in the browser, not sent to Todoist. With it: sent to the courier, which updates the Todoist task; acknowledged only then |
| Tags | Saved in app, not in Todoist |
| Notifications | None: the page needs no Telegram |

A saved prototype result and an acknowledged Todoist update have different statuses on the card and in Activity.

## Its data

`fixtures.js` is generated, not written by hand: `python tools/app_fixtures.py` draws it from the committed exports
and copies the four fictional pictures it shows into `fixtures/` (`--check` compares without writing):

- the photo holds and the unsure scenes from the final photo run, `bench/image_eval_2026-10-22_final.json`;
- the transcript hold (`rec_014`, held by the text gate) and the tasks' project answers, with every project's
  probability, from the server-variants run's CPU row, `bench/server_variants_2026-10-21.json`;
- the task texts from the fixtures' `expected` blocks and the transcript from `bench/fixtures/rec_014_*.json`.

Every model number is Decider's own; nothing is invented and shown as a model answer. The live ledger is not read.

## Files

| File | What |
|---|---|
| `index.html` | the page |
| `state.js` | the rules: each state's actions, what a Save records, the Activity entries (no DOM) |
| `adapter.js` | where items come from and where actions go: fixtures and local storage, or the owner service |
| `app.js` | the views and the simulated viewer |
| `app.css` | phone-width styles |
| `fixtures.js`, `fixtures/` | generated data and pictures |
| `tests.html` | the page's own tests, run headless by `tests/test_app.py` |
