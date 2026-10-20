# CLAUDE.md — read this before changing anything

Ratatoskr Demo is the public spin-off of a private Pocket -> Todoist courier,
built for the AWS Builder "Agent Experience Hackathon: Strands Decider". Keep it
readable: one responsibility per module, short functions, docstrings that say
*why*, test names that read as sentences.

The hard constraints below are carried verbatim from the private repository's
CLAUDE.md §1, with the edits listed under them. Every change must keep all of
them true; tests/test_leak.py, tests/test_cycle.py, tests/test_route.py and
tests/test_devices.py prove several of them. If a change would weaken one, stop
and bring it to the director instead.

Also binding in this repository:

- **Two threads**: the scheduler (main thread) and the Telegram receiver. One
  State object behind one lock, one cycle lock so a `/demo` command and a poll
  never run at the same time. No asyncio, no pools, no HTTP status server.
- **The device envelope** (`ratatoskr_demo/devices/`) is the seam: every input
  reaches the cycle as an `Envelope`, and the watch output is formatted by
  `wrist_adapter.py`. A `Text` envelope (the `/demo` command) is a transcript in
  every respect: triage's tripwire, the source gate, the C9 guard and C1 apply.
- **Device independence** (Dan's ruling, 17 Oct 2026): `[devices] inputs` /
  `outputs` choose the adapters; the courier runs with no wearable at all. The
  cycle talks only to input adapters (`list_new`, `source`, `fetch_text`, via
  `devices/registry.py`), never to `pocket.py`. A secret is required only when
  the adapter that uses it is enabled, and a missing one names that adapter.
- **Triage order is fixed**: keep -> already processed -> not ready -> hold tag
  -> ledger hold -> stop-list tripwire -> under 8 words -> source-text gate ->
  process. The tripwire runs before any model call, Strands Decider included;
  the source gate runs before any Bedrock call. A transcript under 8 words
  (speaker labels not counted; an accidental press) is logged and skipped,
  marked done: no read, no notice (Dan's ruling 14, 18 Oct 2026).
- **Notices carry the recording id only** (Dan's ruling 13, 18 Oct 2026): hold
  notices of every kind, `/status` and the `/demo` reply never carry a Pocket
  title, because Pocket builds titles from the conversation, and never repeat
  a stop-list term. The leak test forbids a title on every shared surface.
- **The source-text gate** (Dan's ruling, 17 Oct 2026, `source_gate.py`):
  with `backend = "decider"`, Strands Decider reads the transcript itself in
  ~300-word windows; sensitive or unsure in any window HOLDS the recording
  (notice: the id only). **Strands Decider is the only model permitted to
  receive transcript text**, besides the extraction itself, because it is
  bound to 127.0.0.1 and persists nothing; the T-LEAK test fails if transcript
  text goes to any other host. Decider down, or a read timed out (Dan's ruling 9,
  18 Oct 2026): nothing is written; the recording waits at the gate and at routing,
  /health is asked before each cycle's first read, a failed read is never re-asked,
  and after three such cycles one count-only notice goes out. The `urgent` tag (Dan's ruling 12):
  only while Strands Decider is unreachable this cycle (no answer from `/health`, or a read
  that failed or timed out), a recording tagged `urgent` skips the source gate, is extracted, and its
  tasks are written to the project with priority high and the label `unrouted` (wrist: "urgent ·
  unrouted"); the tripwire still runs first, a verdict Decider gave is never bypassed (a hold holds),
  an unusable reply is not "unreachable", and with Decider up `urgent` changes nothing. An `ok` release is not gated again.
  An unusable reply Decider gave (an HTTP error, a non-JSON body, no answers) makes the recording
  wait, at the gate and at routing alike, urgent or not; a malformed `sensitive` answer counts as
  unsure and holds the recording (Dan's ruling 23, 19 Oct 2026). With `backend = "decider"` no task
  is ever written with its sensitivity not checked, except under the urgent tag above.
- **Routing** (`route()`) sits between the C9 guard and the Todoist write,
  receives only a guarded `Extraction`, and can only annotate. Backends:
  `"decider"` (default, Strands Decider on 127.0.0.1), `"jev"` (bench only),
  `"none"`. The six questions go in one request over one state: the guarded
  task, its notes and due string, and the contact line. Never the transcript,
  never the title, never a Pocket field. Priority is not asked: three Nouls
  (`someone_waiting`, `hard_deadline`, `worth_researching_soon`), the due date
  counted from the recording's own date (`due.py`; a deadline more than 30
  days out does not count) and `route.priority_rule()` set it, walked in order
  (Dan's ruling 7); an unsure answer at the deciding step means the project
  default, and the task records which step set it or which question was
  unsure. Each project option carries its one-line description;
  `[route] option_descriptions = false` asks with the names alone, for
  measuring only (Dan's ruling 15). `--once --dry-run --save-tasks` writes the
  guarded extractions to the git-ignored `bench/raw/` for the bench's
  `--real-tasks`, and reads recordings the ledger has as done again; a dry run
  writes nothing else.
- **Bedrock** is reached only through `ratatoskr_demo/bedrock.py`, only for
  `openai.gpt-oss-20b`, and every call passes the spend ledger first
  (`spend.py`: worst case reserved before sending, hard stop at $5.00, fail
  closed). Every response's `model` must name the model requested; the model
  record is compared with `config/model_baseline.json` at startup.
- The key is sent only in the Authorization header and never logged.
- Before calling it done: `pytest` green, and make any new check fail on
  purpose once before trusting it (`python tools/check_tests_bite.py`).

---

## 1. Hard constraints (build so that violating these is impossible, then test that)

C1. **No transcript persistence.** Transcript text exists only in process memory during one poll
    cycle. It is never written to `processed.json`, logs, Todoist, Telegram, temp files, or exception
    messages. Log recording ids and titles only. A test must prove this (see §8, T-LEAK).

C2. **No deletion, no sending.** The code has no code path that deletes a Pocket recording (there is
    no such endpoint), no email, no LinkedIn, no outbound message to anyone but Dan's Telegram chat id.

C3. **Hold before extract.** A recording whose title or transcript contains any stop-list term
    (case-insensitive substring; shipped list is exactly `["vargrheim"]`, configurable) is placed on
    HOLD: not sent to the model, not written to Todoist, a one-line Telegram notice sent, id recorded
    with status `hold`. A held recording is released only when Dan tags it `ok` in the Pocket app;
    on the next poll the tag releases it and it is processed normally. Holds are never auto-deleted.

C4. **Tag semantics** (tags come back in the `tags[]` array, match on `name`, case-insensitive):
    - `keep`  → skip forever; never processed, never on the scrub list.
    - `hold`  → same as a stop-list hit (manual hold).
    - `ok`    → releases a hold. Has no effect on a recording that was never held.

C5. **Inbox only.** Tasks go to exactly one Todoist project, named by config (default
    `Ratatoskr Demo`), created if missing. The code never reads, edits, or completes tasks in any
    other project. Every task created carries the label `pocket`.

C6. **Secrets never in the repo.** All secrets come from environment variables (loaded by the config
    module from the git-ignored `.env` file at the repo root). Ship `.env.example` with placeholders.
    Add a test that greps the repo for `pk_`, `sk-ant-`, `apikey_`, and any 40+ hex-or-base64 string
    and fails if found outside `.env.example`.

C7. **Fail loud, never guess — and distinguish terminal from transient.** `error` in the ledger
    means a TERMINAL, data-contract failure only: an unrecognised transcript shape (§4.3), a model
    response that still violates the extraction schema after one retry, or a configuration error
    that needs a human. On a terminal failure: raise, notify Dan on Telegram with the recording id and
    the JSON *keys* (never values), mark the id `error`, continue with the next recording.
    TRANSIENT failures — DNS failure, no route, Anthropic/Todoist/Telegram timeouts or 5xx, HTTP 429,
    NUT unreachable — never mark a recording `error`. The recording stays unprocessed, the cycle
    retries it next poll with exponential backoff per recording (cap 1 h), and if a recording has
    been failing transiently for more than 24 h a single Telegram notice says so (no repeats).
    Tests: one per transient class proving the ledger stays untouched; one proving a terminal
    failure is written once and never retried.

C8. **Idempotent, twice over.** Re-running a poll never creates a duplicate task. `processed.json`
    is the local ledger; an id present there with status `done`, `keep`, or `error` is not
    reprocessed, and `hold` is re-examined each poll only for the `ok` tag. The ledger alone does not
    cover a partial failure (three tasks extracted, Todoist accepts two, the network drops before the
    third and before the ledger write; the next poll would recreate the first two). So every Todoist
    creation also carries a DETERMINISTIC identity: `uuid5(RATATOSKR_NAMESPACE, f"{recording_id}:{task_ordinal}")`.
    Use it as the Todoist command/request idempotency key where the API supports one (the sync
    API's command `uuid`; check the v1 docs for a REST-side equivalent such as a request-id header).
    If the endpoint you use has no idempotency key, fall back to a pre-create check: search the inbox
    project for a task whose description contains `Pocket id: <recording_id> #<ordinal>` and skip
    creation if found. Record in `todoist.py`'s docstring which mechanism you used and why. The
    ledger stays the state of record; the remote key is the second line of defence. Test: mock
    Todoist accepting two of three creates, then failing; re-run the cycle; assert exactly three tasks
    exist.

C9. **Outbound leak guard.** "Do not quote" in the prompt is an instruction, not a guarantee. Before
    ANY model output leaves the process — to Todoist, to Telegram, to the ledger, to the log, to
    `/status` — a local guard compares it against the transcript: normalise both (lower-case,
    collapse whitespace, strip punctuation) and reject the output if any 8-word span of the transcript
    appears verbatim in it (n-gram overlap; make the span length a config value, default 8). On
    rejection: retry the extraction once with a corrective instruction appended ("your previous
    output quoted the transcript; describe, do not quote"); if the retry also fails the guard, place
    the recording on HOLD with reason `leak-guard`, notify Dan with the recording id only, and write
    nothing else. The guard is a pure function in its own module (`guard.py`) so it is testable in
    isolation. Test (extends T-LEAK): an adversarial mock model that returns a distinctive verbatim
    transcript span inside a task `content` field, and separately inside `notes`, and separately
    inside `contact.context`; assert the guard blocks all three, that the retry path is taken once,
    and that after the retry fails the ledger shows `hold`/`leak-guard` and no Todoist or Telegram
    payload contains the span.

### Edits made to the constraints for this repository

- **C3:** the shipped stop-list term is the fictional client name `vargrheim`, with the
  spelling `vargrime`.
- **Read with C3** (Dan's ruling 6, 17 Oct 2026): a stop-list entry is `{term, spellings}`, and
  "contains any stop-list term" also means: contains a listed spelling (any case), or a word, or
  two neighbouring words written together, whose Double Metaphone code matches the term's
  (`Metaphone==0.6`, pinned). Still code only, still before any Bedrock or Decider call; the hold
  reason names the term, never the word heard.
- **C5:** the default project name is `Ratatoskr Demo`; the label stays `pocket`.
- **C6:** secrets are loaded from `.env` by the config module (there is no systemd unit here),
  and the grep also matches `apikey_` keys and any 40+ character hex or base64 run.
  `.env.example` carries `BEDROCK_API_KEY` and `TYPESAFE_API_KEY`; there is no Anthropic key.
- **Read with C7:** the extraction model here is `openai.gpt-oss-20b` on Amazon Bedrock, so
  "Anthropic" reads as Bedrock. There is no NUT client in this repository. A Bedrock call that
  timed out while waiting for its answer is transient and is counted at worst case in the spend
  ledger; a lost network (DNS, no route, refused or reset, connect timeout) is transient and is
  never counted.
- **Read with C9:** there is no `/status` HTTP endpoint here; the Telegram `/status` and `/demo`
  replies are outbound paths, and the guard covers them.
- **Read with C1 and C9:** text sent with `/demo` is a transcript for every purpose above.
- **Read with C1** (Dan's ruling, 17 Oct 2026): besides the extraction, transcript text may be sent
  only to Strands Decider on 127.0.0.1, for the source-text gate. It is still never written
  anywhere, logged, or sent to Jev, to Bedrock outside the extraction, to Telegram or to Todoist.
- **Read with C3:** every sensitivity hold (the source gate's and the post-extraction one) sends
  the recording id only, not the title.
