# BUILD_REPORT — Ratatoskr Demo

Commit hashes in this report refer to the private development history.

Built by Claude Code (Claude Opus 5.5) on 16–17 October 2026 from Dan Lee-Odinson's handoff
(`00_HANDOFF.md`, "ratatoskr-demo (AWS Strands Decider hackathon build)", 16 Oct 2026), with his
rulings from the same session recorded below. Carried over from the private `ratatoskr`
repository at `bc59584`.

## What was built

| Part | Where |
|---|---|
| The courier, carried over and scrubbed: config, Pocket client, transcript rules, triage, extraction, C9 guard, route seam, Todoist writer, Telegram notifier and receiver, ledger, backoff, state, scheduler, CLI | `ratatoskr_demo/` |
| Extraction backend: `openai.gpt-oss-20b` on Amazon Bedrock (bedrock-mantle Chat Completions) | `ratatoskr_demo/bedrock.py`, `extract.py` |
| Spend ledger and hard stop ($5.00), model identity check, model record check, network-loss rules | `ratatoskr_demo/spend.py`, `bedrock.py`, `config/model_baseline.json` |
| Route backend: Strands Decider 2B (local, default), six judgments in one read, the priority set from three of them and the due date by a rule in code; Jev kept for the bench | `ratatoskr_demo/decider.py`, `judge.py`, `jev.py`, `route.py`, `due.py`, `prompts/route.toml` |
| Source-text gate: Decider reads the transcript in ~300-word windows before any Bedrock call; sensitive or unsure holds | `ratatoskr_demo/source_gate.py` |
| Device envelope: `Envelope` and its JSON Schema; input adapters behind one interface, chosen in `[devices]`; Pocket, text (`/demo`), wrist and glasses (stub) adapters | `ratatoskr_demo/devices/` (`registry.py`) |
| `/demo <text>` on Telegram, wrist-shaped messages, 30-second poll | `commands.py`, `devices/wrist_adapter.py`, `config.toml` |
| `--inspect-shape`, `--decider-check`, `--rotate-options N`, `--spend` | `cli.py`, `shape.py`, `rotation.py` |
| The reusable asset: gatekeeper triage | `patterns/gatekeeper_triage/` |
| The bench and its fixtures | `bench/run_bench.py`, `bench/fixtures/`, `bench/RESULTS.md` |
| Decider server wrapper (4-thread cap), Bedrock smoke test, Strands hook live check, mutation check | `tools/` |
| Constraints C1–C9 with the directed edits | `CLAUDE.md` |

Runtime dependencies: `httpx`, and `Metaphone==0.6` for the tripwire's sound match (Dan's ruling 6;
pure Python, BSD, standard library only). Extras: `bench` (typesafe-sdk), `pattern` (strands-agents[openai]
1.61.1), `dev` (both, plus pytest, respx, jsonschema). Strands Decider and torch live in a separate
venv (`.venv-decider`, git-ignored).

## Dan's rulings, 16 October 2026

1. **Demo taxonomy** kept as listed in the handoff (research, school, work, writing, community,
   personal, other, with the listed defaults).
2. **Bench set:** ten invented recordings added (`bench/fixtures/rec_006`–`rec_015`): every
   project, a pleasantry, two sensitive cases. Their `expected` blocks (and those added to
   `rec_001`–`rec_005`) were drafted by Claude Code **for Dan's review**: they are judgment calls,
   and the bench's agreement numbers depend on them.
3. **Wrist line:** priority is always `low` / `med` / `high` (`community · medium · 0.71` would be
   25 characters). The confidence shown is the project answer's; a project below its floor shows
   as `unsorted`.
4. **`prompts/context.md`:** one generic line per demo area.
5. **Strands Decider:** PyPI 0.1.0, as the handoff says. See "Strands Decider" below for its
   concurrency issue and the guard.
6. **Download:** the Decider install and the base model download went ahead on 16 Oct evening.
7. **Bench:** run unattended once Decider is ready, after the estimate below was shown.
8. **Live data:** Claude Code made no contact with Dan's real Pocket, Todoist or Telegram. The
   first live run is Dan's (commands under "Still to do").

## Dan's rulings, 17 October 2026

1. **Latency.** No change to the thread count at any time: Decider stays capped at 4 threads. The
   demo is filmed on one-commitment recordings and `/demo`. The README and this report give the
   measured CPU figure (20–40 s per task, 4 threads, fp32) beside the GPU figure, and say which
   applies. The GPU figure (median 115 ms, 95th percentile 299 ms, on an RTX 3090 under WSL2) is
   in Strands Decider's README, not the v21 model card as the ruling had it, and was measured on the
   v19 checkpoint with one JevBench question per read; it is quoted with those conditions.
2. **Priority.** The three-level Score question is gone. Two Nouls, `waiting_by_date` and
   `worth_researching_soon` (floor 0.70 each), and a rule in `route.py`, in order: waiting yes →
   high; else soon yes → medium; else both answered no at or above their floors → low; else the
   project's default. The task, the wrist line (`prio date|soon|none|default`) and the bench record
   which step set it. Asked afterwards: an unsure `waiting_by_date` with a sure soon yes is
   **medium** (the rule in order). *Superseded by ruling 7.*
3. **The source-text gate.** After the tripwire and before any Bedrock call, Decider asks
   `sensitive` (floor 0.60) of each ~300-word window of the transcript, split on speaker turns;
   any window that is sensitive, or unsure, holds the recording. Decider is the only backend that
   may receive transcript text, and only on loopback; with any other backend the gate is skipped,
   and the README says so. The log gives the window count and the highest p, never text. `/demo`
   text goes through the same gate. Asked afterwards: when Decider cannot be asked, a recording
   tagged `urgent` in Pocket goes on without the gate and everything else waits and is retried;
   both sensitivity hold notices (the source gate's and the post-extraction one) carry the
   recording id only; and the question is worded for a conversation, not a task ("Does this part of
   the conversation contain details that should not go into a shared task list before Dan has
   reviewed them?").
4. **Expected answers exported** for review before the bench numbers are taken as final: every
   expected label with the fixture text it refers to, written outside the repository on 17 Oct.
   The two priority Nouls have no labels of their own; the bench derives them from the expected
   priority under the rule (high: waiting yes; medium: waiting no, soon yes; low: both no; soon is
   not scored when the priority is high). Eleven of the derived answers read doubtfully against the
   new question wording (for example "Expect Mara's slides tonight" is labelled high, so waiting
   yes, though Dan is the one waiting); the export listed them, and ruling 7 settled them.
5. **Device independence.** `[devices] inputs` (any of `text`, `pocket`, `glasses`) and `outputs`
   (`todoist`, `telegram`). A secret is required only when the adapter that uses it is enabled, and
   the error names that adapter. The minimum install is the Bedrock key, the Telegram bot token and
   chat id, the Todoist token, `inputs = ["text"]` and backend `none` or `decider`. The cycle
   reaches recordings only through input adapters, never `pocket.py`; a registry test runs each
   adapter through the full cycle; the README has a "What you need" table.
6. **The tripwire and the transcriber's spelling.** Pocket rendered "Vargrheim" as "Vargrime" in a
   live recording on 17 Oct. Stop-list entries are now `{term, spellings}`. The tripwire holds on
   (a) the term or a listed spelling in the title or transcript, ignoring case, or (b) a Double
   Metaphone match between the term and any word there. Still code only, no model, before any
   Bedrock or Decider call. Asked afterwards: (b) as written ("any single word") cannot catch
   "var grime" (`var` is FR, `grime` KRM, the term FRKRM), so **two neighbouring words written
   together are also matched**; and **the pattern stays standard-library only** with its own
   Soundex (the same join, the same tests), its README naming the package the courier uses. The
   shipped entry is `{term = "vargrheim", spellings = ["vargrime"]}`.
7. **Priority, again: three questions.** Ruling 2's `waiting_by_date` became two Nouls over the
   same state, `someone_waiting` (floor 0.70: "Is a specific, named person expecting a reply or a
   deliverable from Dan, whether or not a date was given?") and `hard_deadline` (floor 0.70: "Is
   there a stated date, time or event by which this must be done, after which it is late or the
   chance is gone?"); `worth_researching_soon` is unchanged. The rule, in code and tested: waiting
   yes → high; deadline yes and the due date within 30 days of the recording (or unparseable) →
   high, a deadline more than 30 days out not counting; else soon yes → medium; else low; the
   step that set it is recorded. The fixtures were relabelled to Dan's table (rec_001 #2 low, March
   being far; rec_001 #3, rec_006 #3, rec_009 #3 medium; rec_008 #2 and rec_015 #2 high; rec_007
   #2, #3, rec_011 #2, rec_012 #1, rec_014 #2, #3 high, as they were), and the three priority
   questions are now **labelled explicitly**, no longer derived from the priority. Asked
   afterwards: (a) neither repository parsed due dates (Todoist parses the due string itself), so
   **a small standard-library parser** (`ratatoskr_demo/due.py`) reads them: a bare month or day
   of the month is the next occurrence after the recording ("March" heard in October is next
   March; "the 18th" heard on the 20th is next month's), and "today" is the recording's timestamp
   from its envelope, never the wall clock, so the bench scores the same whenever it is rerun;
   (b) an unsure `worth_researching_soon` means the project default, recorded as `default:soon`
   (likewise `default:waiting`, `default:deadline`); (c) a far deadline counts as not yes, and in
   general: **walk the steps in order; if the step that would decide rests on an unsure answer,
   stop and use the project default, recording which question was unsure.**
8. **Rulings 7a and 7b**, given before the new bench pass started. (7a) `someone_waiting` asks
   for "a specific person — named, or identified by relationship or role (your sister, my boss,
   the client)", in the question and in its yes/no descriptions; rec_012 #1 (the sister and the
   birthday cake) is now labelled waiting yes and scored. (7b) Every question text and yes/no
   description says "the owner", not "Dan", in `route.toml` and the pattern's `questions.toml`
   (still word for word), and so does the source-text gate's question (`questions.toml`'s
   `source_instruction`, and `SOURCE_INSTRUCTION` in `source_gate.py`, which it mirrors); "his
   attendance" and "his own idea" became "the owner's". A test now fails if a name comes back.
   Approved as built: seasons in the date parser, `worth_researching_soon` unscored on high
   tasks, the other criteria descriptions, the corrected $0.12 worst case.

## Dan's rulings, 18 October 2026 (on the overnight report)

8. **`hard_deadline` kept as built.** No new pass. RESULTS.md and this report give it as measured:
   Decider reads deadlines strictly (12 confident "no" on tasks labelled yes; its floor does not
   protect this question; priority agreement 16 of 30 against 21 and 20), and likewise its
   confident `someone_waiting` misses. The per-question "sure and right" tables are the claim: no
   summary may say Decider is never confidently wrong without naming the questions it holds for.
9. **Decider down, or a read timed out: nothing is written.** At the gate the recording waits (as
   built; the `urgent` bypass unchanged); at routing the task waits too: no Todoist write, no
   unrouted task, no "sens not checked" line. The next cycle checks `/health` before any read; a
   timed-out read is never re-asked. After three consecutive cycles without Decider, one notice
   with counts only ("N items waiting on Decider"). Tests per branch; mutations for writing while
   Decider is down. (Built after the first commit of 18 Oct.)
10. **No other project named.** "Another long-running Bedrock experiment on the same laptop"
    replaces that experiment's name in RESULTS.md, this report, the README and the docstrings; its
    worker counts and dates do not appear in the public repository.
11. **History.** The private repository is not rewritten. At submission the public
    `ratatoskr-demo` is created fresh, with a history that starts at `09790cd`: everything up to
    it squashed into one "Initial import" commit. The procedure is under "Publish (submission
    day)", tested on a throwaway clone. Nothing goes anywhere public until Dan says so.
12. **The urgent tag, bounded:** only while Strands Decider is unreachable this cycle (no answer
    from `/health`, or a read that failed or timed out), a recording tagged `urgent` skips the
    source gate, is extracted, and its tasks are written to the project with priority high and
    the label `unrouted` (wrist: "urgent · unrouted"). The tripwire still runs first; a verdict
    Decider gave is never bypassed (a hold holds); an unusable reply is not "unreachable"; with
    Decider up, `urgent` changes nothing. Tests per branch, and mutations that remove the
    "Decider unreachable" condition.

## Dan's rulings, 18 October 2026 (after the first live run)

13. **Notices carry the recording id only.** The tripwire's hold notice printed the recording's
    Pocket title, and Pocket builds titles from the conversation, so the title carried the
    protected name. Hold notices of every kind now read `Held <id>: <reason>.` (a stop-list hold
    says `stop-list`, not the term); `/status` lists holds by id; the `/demo` reply to a tripwire
    hold names no term. The leak test now plants a title carrying the protected name on the
    stop-listed fixture and forbids it in every Telegram payload, every Todoist payload, the
    `/status` reply and what reaches `route()`.
14. **A transcript under 8 words is skipped before the source gate**: logged, no Decider read, no
    notice, marked done (reason `too-short`) so it is not looked at again. Speaker labels
    ("Speaker 1:") are not counted as words. A `/demo` text under 8 words gets "skipped · under 8
    words". The tripwire still runs first.
15. **The project options and their descriptions.** (a) Checked: the seven options in
    `route.toml` and the pattern's `questions.toml` already carry `prompts/context.md`'s one-line
    descriptions as option text (word for word but for commas and "and"; `work` adds "Paid work
    in any form:", and `other` has no line in `context.md`), so **nothing was changed**; a test
    now fails if a `context.md` area loses its option or its words. (b) `[route]
    option_descriptions` (default true; false asks with the option names alone, for measuring
    only) and the bench's `--option-descriptions on|off|both`: `both` asks every task twice and
    RESULTS.md sets agreement and sureness side by side. A real-task set: `ratatoskr-demo --once
    --dry-run --save-tasks [--demo-text "<text>"] [--option-descriptions on|off]` saves each guarded
    extraction (never the transcript) to the git-ignored `bench/raw/`, and the bench's
    `--real-tasks FILE` asks those tasks too, reported as counts without their text (they have no
    labels to score against). The re-measure runs on Dan's word (see "Still to do"); until then
    RESULTS.md and the README keep the 17–18 Oct numbers, labelled **[as measured]**.
16. **Publish: the replay approved.** `475f532` and `7e4d216` are replayed as one public commit on
    top of the squashed "Initial import"; the private history is not touched; the names file stays
    outside the repository (procedure and test under "Publish (submission day)").
17. **The re-measure: approved** (run 19 Oct, under "Bench"). Worst case $0.21 approved. Both conditions ask
    the same tasks, those saved by the dry run with the descriptions on. Since 15(a) found the
    descriptions already present, the side-by-side measures what they are worth; the real-task set
    is the measurement that matters. Dan gave the `/demo` sentence; it is a transcript, so it is
    not written here or anywhere else in the repository (C1). Nothing starts until Dan says the
    other experiment on the laptop is paused.
18. **README: privacy as a heading.** The four refusals (the tripwire, the source-text gate, the
    task-text sensitivity hold, nothing written while Decider is down) are presented together
    under "Privacy: content sensitivity checked and protected", opening with one sentence: privacy
    is a feature of the design, and these refusals are how it is built. The pattern's README
    puts its description of the gate under the same heading. Docs only; in the 19 Oct commit.
19. **`media/`: images in, audio and video out.** The folder is Dan's, from his animation tool.
    Committed: the logo PNGs (the 2400×600 banner and its copper and white one-colour versions,
    the 512 mark and its two one-colour versions, the two 32 px marks) and the 1600×900 hero
    still; none carries embedded text metadata. The banner heads the README and the hero sits
    below it. `.gitignore` keeps out mp4, mov, webm, wav, mp3, m4a and any manifest, written
    `media/**/*.<ext>` because the audio and the draft videos sit in subfolders, which `media/*.<ext>`
    would not reach, plus `media/**/*.zip` (a zip of the cleaned audio). All 39 such files were
    checked ignored. The audio was not opened. The avatar and the tool's status, rules and cleanup
    files stay untracked, not committed.
20. **The C6 scan follows git's view.** It covers exactly the files `git ls-files --cached --others
    --exclude-standard` lists (tracked, and untracked ones `.gitignore` does not keep out) and
    nothing else, so ignored files, the media's audio and video among them, are never opened; a
    binary file (a null byte in its first 8 KB, read in binary mode) is skipped, never read as
    text. The animation tool's bookkeeping files are ignored (`media/**/*.json`, `media/**/*.csv`,
    at any depth as in ruling 19). A test plants a 40-hex string in an ignored, a tracked and an
    untracked file and in a binary one; a mutation drops `--exclude-standard`. The `.env` file is
    still skipped by name as well, whatever `.gitignore` says. And for the article: the real-task
    result, 0 of 5 sure of the project with and without the descriptions, is the number it leads
    with for real recordings; the fixture numbers stand beside it, labelled as fixtures.
21. **README: "Set up your projects and your stop list".** A walk-through for someone who has
    never opened the repository, in five numbered steps, with the file and field names as they
    are: the project list (`[project.criteria]` and `[project.defaults]` in `route.toml`, the areas
    in `context.md`, local copies in `prompts/`); how to write a description that sorts, with a
    good and a weak example and, where that step ends, what the 19 Oct measurement showed; the
    stop list (`[triage] stop_list`, code, never a model); the Todoist project, the labels and
    what "unsorted" on the wrist means; and the checks before the first run (`--decider-check`,
    then `--once --dry-run`) with the lines a good run prints. No new code and no new config
    field; docs only.
22. **README: the privacy rule, outright.** At the top of "Privacy: content sensitivity checked
    and protected", in bold: when a privacy check is unsure, the recording is held, never passed
    through; the only outcome of an uncertain answer is an id-only notice back to the owner,
    released by a tag. Then one sentence separating it from the project sort, which is not a
    privacy question (the conversation is already cleared) and files "unsorted" when unsure.
23. **Nothing written with its sensitivity not checked (backend "decider").** Before this, a
    routing reply Decider gave but the courier could not use (an HTTP error, a non-JSON body, no
    answers) left the task unrouted and wrote it, marked "sensitivity not checked", and so did a
    malformed `sensitive` answer. Now: an unusable routing reply makes the recording wait, exactly
    as the same reply does at the source gate (`DeciderRejected` is a `RouterRejected`; `route()`
    asks nothing more and reports it in `Routed.rejected`; the cycle raises the same wait, urgent or
    not, and does not mark Decider down); a malformed `sensitive` answer counts as unsure and holds
    the recording with the id-only notice `Held <id>: sensitive-unsure. Tag it ok in Pocket to
    release it.` The shared `judge.sensitivity_of` is unchanged, so the bench-only Jev router keeps
    its behaviour. The urgent exception (ruling 12) is untouched: a task written under it, while
    Decider is unreachable, still shows "sens not checked" on the wrist, which is true of it, and
    with backend "decider" that is now the only way that line appears. Tests per branch
    (`tests/test_unusable_replies.py`: four kinds of unusable reply, the same with the urgent tag,
    the recovery, two kinds of malformed answer), five mutations, the leak sweep unchanged.

## Dan's rulings, 20 October 2026

24. **README: "System requirements"**, after "What you need": the host table, the measured latency
    and the recommended settings, every bracketed value read from the code and every measured
    number checked against RESULTS.md and the overnight report. Where the draft disagreed with the
    record or the code it was corrected (ruling 26 accepted every correction): the reference laptop
    has 16 logical cores with Decider held to four, not a four-thread CPU; the 30 s median of 17-18
    Oct was measured with the laptop otherwise idle, and with another job keeping every core busy
    reads did not finish at all (0 of 44); the $5.00 hard stop is a ceiling in code that config may
    lower and never raise; the project descriptions the sort reads are `[project.criteria]` in
    `prompts/route.toml`, not `prompts/context.md`; the courier needs `httpx` and `Metaphone`, and
    only the pattern's client is standard library. No GPU bullet (`serve_decider.py` hard-codes the
    CPU) and no pointer to the article (the README does not mention it).
25. **README: "Safety" and "Terms of use"**, with every claim proved against the code or a test
    before it is written, and a section left unwritten while any claim in it fails. Three claims
    were true but untested, and got tests first: no eight-word span of any fixture transcript in
    any output of a full cycle or in the committed RESULTS.md, `.env` git-ignored (asked of git),
    and the Decider server's contact with the Hugging Face Hub (`tests/test_hf_offline.py`, through
    `tools/hf_offline_check.py`).
26. **Decisions on 24 and 25.** The corrections to 24 accepted; `media/**/*.txt` and
    `media/archive/` ignored (the animation tool's notes, which the secrets grep flagged; Dan
    checks them himself); replacement wording for the ten claims of 25 that failed. Of those, the
    Terms of use replacements hold. Two Safety replacements still do not: the leak guard's hold
    notice (`Held <id>: leak-guard. Nothing was written.`) has no release instruction, though an
    `ok` tag releases it like any hold; and an unreadable answer or no answer at routing makes the
    recording wait rather than hold (only a malformed `sensitive` answer holds it), while the
    transcript has already gone to Bedrock for the extraction by then, and under the `urgent` tag
    it goes there without the gate. The Safety section waits for Dan's wording.
27. **The license, completed.** `LICENSE`: sections 1-9 byte for byte apache.org's text (sha256 of
    those 9,919 bytes recorded in `tests/test_license.py`), the appendix now `Copyright 2026 Dan
    Lee-Odinson`. `NOTICE`: the holder, then one line per third-party component the courier, the
    Decider server and the optional extras depend on at run time, 2 model weights and 84 packages,
    each with its license as its model card or package metadata states it (none unstated); none is
    in the repository. The appendix's short header heads all 45 first-party `.py` files in
    `ratatoskr_demo/` (the package: there is no `src/`), `patterns/`, `tools/` and `bench/`; the
    example's line-count test now counts below the header. `pyproject.toml` gains `authors`. The
    README's license line, and the placeholder grep in the publish check. RESULTS.md re-rendered
    (no model asked) for `run_bench.py`'s new blob.
28. **Safety claims 1 and 2, the packaging, the public release.** Claim 1: the code changed, not
    the sentence; the leak guard's notice now reads `Held <id>: leak-guard. Tag it ok in Pocket to
    release it.`, like every other hold notice, and a test holds all five kinds of hold (stop-list,
    hold tag, source gate, after extraction, leak guard) to the release instruction and no title.
    Claim 2 reworded to what the code does (a below-floor answer holds at the gate and at routing,
    a malformed one holds at routing, no answer or an unusable reply makes the recording wait), with
    a new test that a below-floor answer at routing holds with Decider. Both sections are in the
    README. `pyproject.toml`: `setuptools>=77` (the SPDX license string needs it) and `NOTICE` in
    `license-files`. The public repository is built as one commit holding this tree, under the
    procedure in ruling 28; the private history stays private.

## Verify items: what was found

### Bedrock (bedrock-mantle)

- **Smoke test, 16 Oct 2026:** `GET /v1/models` lists 60 models, `openai.gpt-oss-20b` among them.
  A tiny chat completion answered `ready`; the response's `model` was exactly
  `openai.gpt-oss-20b`. Cost: $0.000011.
- **Model record:** saved as `config/model_baseline.json`. Mantle returns
  `data_retention.allowed_modes` in a **different order on every call** (four calls, four orders,
  same four values), so a literal comparison would stop every run. Lists of plain values are
  compared as sets; every value is still compared, and a test proves a new mode is a difference.
- **`reasoning_effort`:** validated and honored. `"bogus"` is rejected with HTTP 400; the same
  prompt used 137 output tokens at `low` and 335 at `high`. Kept at `low`.
- **Structured output path: JSON in text** (the handoff's third path). Measured on the synthetic
  fixtures `rec_001` and `rec_005`, five calls each, reasoning effort low:
  - `response_format` json_schema (strict): accepted by mantle, but **0/10** replies were valid
    JSON (a stray leading brace, `{\n{"contact"...`, or a code fence). The schema is not enforced.
  - Tool calling, the schema as the one forced tool: **4/10** valid.
  - JSON in text, schema in the system prompt: **10/10** valid with no repair.

  The reply is validated strictly; the only leniency is unwrapping a whole-reply code fence. The
  existing one corrective retry stays. Recorded in `extract.py`'s docstring.
- **Prices:** $0.07 per 1M input tokens and $0.30 per 1M output tokens, us-east-1, Standard tier,
  confirmed on 16 Oct from the AWS Price List API (`USE1-openai.gpt-oss-20b-mantle-input-tokens-
  standard`, publication date 2026-10-16) and matching the handoff. Model card: 128K context, 16K
  maximum output, reasoning on by default.
- **Extraction quality, first live dry run on the synthetic fixtures:** `rec_001` gave two of its
  three commitments and attributed one promise to the wrong speaker; `rec_005` gave none, as
  expected. The bench measures this properly.

### Strands Decider

- **Version installed:** `strands-decider` 0.1.0 from PyPI (uploaded 1 Oct 2026, built from commit
  `fe39fad9ec8e`); torch 2.14.1+cpu, transformers 5.20.1. Model
  `StrandsAgents/strands-decider-2B-hobson-v21` (Hugging Face revision `2b52a6235c1b`) on the
  Qwen3.5-2B base.
- **Docs read:** README, `docs/inference.md`, `schema.py` and `server.py` at main `930e6d5de163`
  (16 Oct 2026).
- **Request and response shape:** `POST /v1/systemone` with `{"state", "questions"}`; question types
  `choice` (criteria: option → description), `score` (criteria: levels, lowest first) and `noul`
  (criteria: `true` / `false`). Answers: choice → `choice`, `probabilities`, `confidence`; score →
  `score`, `probabilities`, `confidence`; noul → `noul` (the probability of yes, **no confidence
  field**, so the private repository's `max(p, 1 − p)` rule carries over). `GET /health` names the
  checkpoint and device. The Decider docs say Jev compatibility is not verified, so Ratatoskr Demo
  builds Decider requests itself (`decider.py`) rather than through the TypeSafe SDK.
- **Concurrency issue:** 0.1.0 can return one request's answers to another when two arrive at the
  same moment (its issue #17, fixed on main after the release). Inside the courier, a cycle lock
  already serializes polls and `/demo`. Between processes, `--loop` holds an OS lock on
  `ledger/loop.lock`, and the bench, `--rotate-options`, `--decider-check` and the hook check
  refuse to start while it is held.
- **Thread cap:** 0.1.0 has no threads flag and never calls `torch.set_num_threads`, so
  `tools/serve_decider.py` sets `OMP_NUM_THREADS`/`MKL_NUM_THREADS` to 4 before importing torch,
  calls `torch.set_num_threads(4)` and `torch.set_num_interop_threads(1)` in the serving process
  (it reported intra-op 4, inter-op 1), and asks for below-normal process priority.
- **Model card on option order:** the v21 card says nothing about option-order bias; the README
  says option order is reshuffled in training. The rotation test measures it (below).
- **Download:** the first serve fetched the Qwen3.5-2B base (about 4.5 GB) at about 340 KB/s on
  16 Oct; Hugging Face's xet transfer had throttled itself to one connection, so the server was
  restarted with `HF_HUB_DISABLE_XET=1` (same rate: the link was the limit).
- **Latency on this laptop: about 30–40 seconds per read, not the 1–2 s the handoff expected.**
  Measured on 17 Oct on a fresh server, 8 sequential reads of different canonical tasks (four
  questions each, about 770 input tokens after the prefix cache), 4 threads at below-normal
  priority: 28–44 s, steady memory about 8 GB. With the prefix cache off: 46–94 s and about 1,140
  tokens per read, so the cache stays on. The arithmetic agrees: roughly 770 tokens through a
  2-billion-parameter model in fp32 is about 3 trillion floating-point operations, around 30 s on
  four laptop cores. Consequences: `[route] decider_timeout_seconds` is now 180 (60 was too close),
  and a recording with three commitments spends about 1.5–2 minutes in routing. Dan's ruling of
  17 Oct keeps the 4-thread cap; see "Dan's rulings, 17 October 2026", item 1, for the GPU figure
  this is set beside.
- **Hangs:** three, all after long runs of reads. See "Decider hang (finding)" below.
- **Slow start once:** on 17 Oct 10:58 the server took six minutes to answer `/health` (21 s the
  time before), holding one HTTPS connection to a CDN address with no new file in the Hugging Face
  cache: the hub's online revision check, not a download. It then served normally. Setting
  `HF_HUB_OFFLINE=1` for the server would skip the check, since the model is cached; not changed.

### Decider hang (finding)

Strands Decider 0.1.0 hung three times on 17 Oct 2026, each time well into a long run of reads,
each time on a server started fresh for that run (4 threads, below-normal priority, prefix cache
on). "Hung" here means: `/health` gets no answer within 10–20 s, the bench's reads overrun its
180 s timeout one after another (each timed-out request is abandoned by the client and a new
connection opened), and no read result comes back for minutes at a time.

| | run | step when found | time into the run | reads completed | what else was running |
|---|---|---|---|---|---|
| 1 | first overnight chain, server up 23:38 | after the chain ended at 02:04 (a check, a 2-order rotation, the first bench, the hook check) | about 2 h 30 min of serving | about 294 (1 + 60 + about 233) | nothing heavy; one request had just timed out and been abandoned mid-read |
| 2 | ruling-2 bench, server up 11:04 | the 7-order rotation (began about 13:00) | 3 h 26 min | at least 31 logged (the server log is block-buffered, so a lower bound) | the test suite twice and single-mutation checks, about 12:40–13:10 (mine), and another long-running Bedrock experiment on the same laptop |
| 3 | ruling-7 bench, server up 14:36 | the rotation (began 15:15, after 59 reads of gate, extraction and canonical passes in 38 min) | 3 h 39 min when found, the first timeout seen at 18:19 | at least 190 logged, so at least about 131 of the 210 rotation reads | **nothing** from this build; another long-running Bedrock experiment on the same laptop was running |
| 4 | the option-2 pass, server up 18:54 | from the 10th source-gate read on, then every six-question read | about 10 min | 9 gate reads; 0 of 44 six-question reads | **another long-running Bedrock experiment on the same laptop**, its workers keeping most cores busy at normal priority; the memory guard stopped the server at 16.6 GB commit |

Hang 1 used four cores throughout. Hang 2 was found at 7.6 cores (201 threads) and was put down to
CPU contention from the test runs; hang 3, with nothing else running, shows that was not the
whole story. Each was stopped by its exact PID; no run wrote its results, because the bench
writes only at the end.

**The capture of hang 3, taken before anything was stopped** (18:42, Decider PID 9924, started
14:36; `ledger/decider-hang-2026-10-17.txt`):

- working set 12.94 GB (peak 13.27 GB); **commit (private bytes) 20.54 GB**, up from 17.8 GB at
  18:16 (26 minutes earlier) while it was hung; virtual 27.64 GB;
- 189 threads (152 Ready, 8 Running, 19 Wait/Unknown, 8 Wait/UserRequest, 2 Wait/EventPairLow);
  881 handles;
- CPU 22,346 s in 4 h 6 min (1.5 cores on average); in spells: 0.0–0.1 cores for two minutes at
  18:19–18:21, 7.5–7.7 cores at 18:15 and 18:41–18:42;
- system: 34.1 of 63.7 GB of RAM in use, commit 47.7 of 71.7 GB, the page file nearly unused (no
  paging);
- the server log: start-up lines, four `/health` answers at start-up, then 190 lines of
  `POST /v1/systemone 200 OK` (the last 50 all from one connection), and no error or traceback
  anywhere. transformers warned at start-up that two kernels fall back to their reference
  PyTorch implementations ("correct but much slower"): `causal_conv1d` and `flash-linear-attention`
  are not installed (both are GPU kernels).

**Reading: memory growth, not a flat stall.** The working set is about 13 GB against about 8 GB
steady at start-up (measured on 17 Oct), commit grew by 2.7 GB in 26 minutes while no read was
completing, and most threads were runnable (Ready or Running), not waiting. One candidate, not
proven: the prefix cache, which keeps a growing number of entries during a rotation (7 orders of
the project options x 30 tasks gives 210 distinct prompts), with each read slower than the last
until reads overrun the client's timeout; then abandoned reads overlap new ones (the 7.5-core
spells, two reads of four threads) and 0.1.0's known concurrency issue (#17) becomes possible.
The second full run of the morning (02:30–04:49) completed its rotation on a fresh server without
a hang, so it is not deterministic. Consequences: rotation runs are now written task by task and
restart Decider on a hang (below, "Decisions", 28); the live run starts on a fresh server and
checks `/health` before each scripted sentence.

**The overnight test, 17–18 Oct, with another long-running Bedrock experiment on the same laptop paused and nothing else
running** (Decider at below-normal priority, 4 threads, `--max-commit-gb 16`, 600 s per read):

- *The pass* (23:26–23:52): commit 9.94 GB at start-up (working set 7.58 GB), 10.34 GB after 58
  reads (12 source-gate windows, 16 extracted and 30 canonical tasks) and the hook check; every read
  answered, about 30 s each.
- *The rotation re-measure* (00:03–01:57, `bench/rotation_run.py`, three servers of 10 tasks, 70
  reads each): commit 9.94 GB at each start; 10.30 GB after the first task; 10.34 after task 10;
  10.36 after task 20, the peak (working set 7.89 GB); 10.36 after task 30. Each task (7 reads)
  took about 3.5 minutes and about 820 CPU-seconds, so about 30 s and 117 CPU-seconds per read.
  Every one of the 210 reads was answered, no read failed and no unscheduled restart happened.
- *Determinism:* after each scheduled restart, the previous task was asked again in all 7 orders on
  the fresh server: task 10 ("school" x 7) matched 7 of 7, task 20 ("community" x 7) matched 7 of 7.

**Revised reading.** With nothing competing for the CPU, memory rose by about 0.4 GB on the first
read and then by at most 0.06 GB over the next 69, and every read was answered. The growth and the
hangs of 17 Oct came with CPU contention: hang 2 during test runs and the other experiment, hang 4
with the other experiment's workers busy (and it was running during hangs 1 and 3, at a load not
measured then). The leading explanation is now starvation: at below-normal priority, Decider gets little CPU
while other work runs at normal priority, a read overruns the client's timeout, the abandoned read
keeps running in one of Decider's 24 slots beside the next one, and memory and CPU climb (the
7.5-core spells) until reads stop completing. Not shown: whether memory grows over many hundreds of
reads on one server with no contention (the servers here served at most about 70; the 17 Oct
morning run served about 300 without a hang). The bench and the rotation run now wait 600 s per
read (the courier keeps 180 s), restart Decider every 10 tasks, and stop it past 16 GB of commit.

### Strands Agents SDK

- **Version:** `strands-agents` 1.61.1 (15 Oct 2026; tag `python/v1.61.1` at `5cb880559e5e`),
  pinned in `pyproject.toml`.
- **Hook API**, read in the installed source (`strands/hooks/events.py`, `registry.py`):
  `BeforeToolCallEvent` carries `tool_use` (name, input, toolUseId) and a writable `cancel_tool`;
  a `HookProvider` registers with `registry.add_callback(BeforeToolCallEvent, ...)`.
- **Provider:** `strands.models.openai.OpenAIModel(client_args={...} | client=..., model_id,
  params, stream)`. With `client_args`, Strands builds and closes a new `AsyncOpenAI` per request;
  with `client=`, it never closes the client. The live hook check therefore passes `client=`.

### TypeSafe Jev (bench only)

- Price from https://docs.typesafe.ai/models, read 16 Oct 2026: $0.042 per 1M input tokens, output
  tokens free; `jev-latest` is `jev-1.13.0`.

### The real Pocket transcript shape

**Not yet confirmed: it needs Dan's device.** `ratatoskr-demo --inspect-shape <recording_id>`
prints the key names and types of one real recording (no values, no text). If the shape differs
from the fixtures' plain string, `transcript.extract_text()` is fixed here and the fix carried back.

## Decisions made that the handoff left open

1. **Not carried over** (not on §4's list): the private repository's HTTP `/status` server, the
   daily summary job, the power job, and `ratatoskr route-check`. So there are **two threads**
   (the scheduler and the Telegram receiver). The private rule "the Telegram notice says
   sensitivity not checked" lives on the wrist line (`sens not checked`), since there is no daily
   summary.
2. **§8 says to copy the safeguards from another long-running Bedrock experiment on the same laptop; §13 forbids opening any file
   of that experiment's repository.** §13 won: the spend ledger, identity check, record check and
   network-loss rules were written fresh from §8's description.
3. **Spend ledger mechanics:** money in whole nanodollars, rounded up; the input-token upper
   bound is the request's UTF-8 byte count plus 256 (gpt-oss's byte-level tokenizer cannot produce
   more tokens than bytes); a cross-process lock file, since the courier and the bench share one
   ledger; an unreadable ledger refuses every call. `[spend] hard_stop_usd` may be lowered in
   config, never raised past $5.00.
4. **"Reset connection" under §8:** a reset while connecting is a lost network (released, never
   counted). A reset or disconnect after the request was written counts as "sent and never
   answered" and stays at worst case, which is the fail-closed reading of the two rules together.
   An HTTP error response also stays at worst case (no usage is reported).
5. **Network loss in the courier:** the recording is retried by the cycle's backoff, never sooner
   than 60 seconds. The bench waits 60 seconds, checks a TCP connection to the endpoint, and
   retries the unit of work, up to three times.
6. **Model allowlist in config:** any `model_id` other than `openai.gpt-oss-20b` is a startup
   error, as is a base URL that is not `https://bedrock-mantle.<region>.api.aws`, and a
   `decider_url` that is not on this machine.
7. **Secrets:** `config.py` reads `.env` itself (its values win over the process environment);
   errors give line numbers, never content. The tests never read `.env`: an autouse guard points
   the config at a missing file and makes any non-loopback connection or DNS lookup fail.
8. **`/demo` details:** the ledger id is `tg-<message_id>` (a re-delivered update is handled
   once); the reply replaces the normal notices; a sensitive `/demo` hold replies with the five
   judgments but no task text (the private rule that a sensitive hold's notice carries no task
   text); a held `/demo` text cannot be released (it has no Pocket tags).
9. **Pocket notices:** one wrist-shaped message per task, and no "Done" header line, so the watch
   shows the task first. Hold notices keep their one-line form.
10. **The state Decider sees** is the private Jev router's: task content, notes and **due string**,
    plus the contact line. §7 said "task text plus notes plus the contact line"; the due string
    is part of the task and is what the 30-day test reads.
11. **Decider down:** since Dan's ruling 9 (18 Oct), nothing is written while Strands Decider is
    missing; see 29. An unusable reply (HTTP 400 / 404 / 422, a malformed body) still writes the
    tasks unrouted with `sens not checked` and tells Dan once. A server serving a different
    checkpoint is a configuration error.
12. **The pattern fails closed:** `patterns/gatekeeper_triage` holds when Decider cannot be asked
    (`on_unavailable = "hold"`, configurable). A general-purpose gate should not inherit the
    courier's "write and say so".
13. **Option-order test:** N rotations of the project list, one step each; a task "flips" if its
    chosen project is not the same in every order; the first-pick rate is reported too.
14. **Bench scoring:** an extracted task is matched to the one canonical task whose keyword
    group appears in it; unmatched tasks are counted, not scored. Agreement, "sure" (at or above the
    floor), agreement when sure, and the gate (hold or not) are reported per question. The
    gpt-oss column answers in JSON with self-reported confidences, read by the same rules.
15. **Commits** carry a `Co-Authored-By: Claude` trailer, matching the README's AI-use disclosure.
16. **A second bench view, on the canonical tasks.** The first bench run showed the extraction
    dropping the very details that made two tasks sensitive (a colleague's medical leave, an
    acquisition under NDA), so "sensitive" was scored against task text that no longer contained
    them. The bench now also asks every backend about the 30 canonical tasks as written, which
    measures the routers apart from the extraction. The extracted-task view is unchanged.
17. **Decider timeout raised to 180 s** (`[route] decider_timeout_seconds`, the router client and
    the pattern's client), from 60, after the latency measurements above.

Left open by the rulings of 17 October, and decided here:

18. **The input adapter interface has a third method.** The ruling named `list_new` and
    `fetch_text`; triage needs a recording's title, tags and time before any text is fetched (the
    `keep`, `ok`, `hold` and `urgent` tags, the lookback window), so every adapter also has
    `source(item_id)`, which returns those and never text.
19. **What "only Decider may receive transcript text" allows.** The extraction call to Bedrock is
    the courier's purpose and still receives the transcript; the rule is read as: besides the
    extraction, transcript text goes only to Decider on 127.0.0.1. The T-LEAK test plants a
    sentinel and fails if it reaches Decider on any host but loopback, a routing request, a log,
    a state file, Telegram or Todoist; a mutation that points the gate's client at `decider.lan`
    turns it red.
20. **The gate stops at the first sensitive window**, since one is enough to hold; the log says how
    many of how many windows were asked (`2 of 4 window(s) asked, max p=0.91 -> sensitive`). An
    unsure window does not stop it: a later window may still be sensitive, which is the stronger
    reason. The window size is `[route] source_window_words` (default 300, allowed 50–1500).
21. **A recording released with `ok` is not gated again**: the tag is Dan's review, and gating it
    again would hold it forever.
22. **The `text` input requires the `telegram` output** (`/demo` arrives by Telegram); config
    refuses the combination otherwise. With the `todoist` output off, nothing is written, the
    ledger marks the recording done, and the wrist message still goes out. With the `telegram`
    output off, holds and results are logged only.
23. **`jev.py` reads answers through `judge.py`**, the same rules as Decider (floors, the priority
    rule), so the bench compares answers, not interpreters. It is no longer byte-identical with the
    private repository's `jev.py`, and its docstring says so.
24. **Bench scoring of the priority:** since ruling 7 each priority Noul is scored against its own
    label; the priority itself counts as "sure" when a Noul's step set it, not a project default.
    The labels were drafted for Dan's review, each set so that the rule, given the labels and the
    due date, yields his expected priority (a test checks all 30). `worth_researching_soon` is
    left unlabelled (not scored) on high tasks, where the rule never reaches it (approved). Every
    `someone_waiting` is labelled since ruling 7a. The export lists the judgment calls.
25. **The phonetic package:** `Metaphone` 0.6 (PyPI, BSD; sdist sha256 `ad0beadc…53da27`), 711
    lines of pure Python whose only import is `unicodedata`; read before it was installed. Its
    Double Metaphone codes are not cut to four characters, so "Vargrheim" is FRKRM and "Varga"
    (rec_008's contact) FRK: no match. Codes are computed per call, not cached across calls, so no
    transcript word outlives the cycle. The phonetic match uses the term, not its spellings (each
    spelling is matched as written). A short term has a short code and will match more words;
    that fails closed (a hold). On the 15 fixture recordings only rec_002 trips, in the courier
    and in the pattern; a test keeps it so. The hold reason stays `stoplist:<term>`, never the
    word heard.
26. **The due-date parser** recognises: today / tonight / this morning, tomorrow, weekdays (the
    next one on or after the recording's date), this / next week, the weekend, end of the month,
    next month, "in N days / weeks / months", next year, month names with or without a day and a
    year ("may" only beside a day, a year or "in" / "by", since it is also a verb), ordinals ("the
    18th", "week of the 20th"), ISO dates and seasons (1 Mar, 1 Jun, 1 Sep, 1 Dec; "fall" only
    after "in" / "this" / "next"). Seasons were not in the option Dan approved; they were added
    because "spring" is how rec_001's hiring is phrased and an unreadable one would count as
    near. The earliest date named wins. Times of day are ignored. "Before the next call" names no
    date, so near. Only rec_001's "March" is more than 30 days out among the fixtures.
27. **The rotation in RESULTS.md is the 04:49 one, carried over.** The plan was to carry over the
    rotation of the run under way when ruling 7 arrived; that run hung (hang 2) and wrote nothing.
    Dan then chose to re-measure in the next run, which hung too (hang 3). Dan's ruling after it:
    the six-question pass runs with the rotation of the morning's clean run (17 Oct 04:49 local:
    30 tasks x 7 orders, 0 unanswered, 3 of 30 flips, 10.0%, first-listed 13.8%) carried over and
    labelled as measured with the pre-7b wording of the project question ("Which of Dan's projects
    or areas..."); the re-measure with today's wording runs later, overnight, as an addendum, kept
    beside the 04:49 figures: if the flip rate moves, the wording changed the order sensitivity; if
    not, that is evidence of stability. `bench/run_bench.py --rotations 0 --rotation-from FILE
    --rotation-note TEXT` rebuilds a rotation from recorded choices, refusing a file whose canonical
    tasks or options differ, and says in RESULTS.md where it came from. The estimate now also counts
    the canonical pass's Bedrock calls, which the earlier "$0.087 worst case" left out.
28. **A rotation run that survives a hang** (Dan's ruling after hang 3), `bench/rotation_run.py`:
    each task's picks are appended to `bench/raw/rotation-<stamp>.jsonl` and flushed as the task
    finishes; it runs its own Decider, checks `/health` before every task, and on no answer or a
    failed read stops the server by the exact PID in its pid file (only if that PID's command line
    is `tools/serve_decider.py`), starts a fresh one and asks the task again (three tries, then it
    stops and can be resumed with `--resume`); it restarts the server every 10 tasks besides; and it
    refuses to start while anything already answers on the Decider URL, so it never touches a
    server it did not start. The file records the project question's wording, and the bench says
    whether it matches today's. `tools/serve_decider.py --max-commit-gb N` exits the server, saying
    why, once its committed memory passes N GB, so it is restarted fresh instead of hanging; the
    rotation run uses 16, and so does the live-run sheet, which also starts on a fresh server and
    checks `/health` before each scripted sentence.
29. **Ruling 9, as built.** With backend `"decider"`, the cycle asks `/health` (10 s timeout)
    before its first Decider read, once per cycle, and a /demo text asks afresh. No answer, or a
    read that fails or times out at the gate or at routing: that recording waits with nothing
    extracted or written (no Todoist task, no unrouted task, no wrist message), the read is never
    asked again, and **no further read is sent in that cycle** (a stricter reading than "never
    re-asked": a read that timed out may still be running inside Decider, and a new one beside it
    is how the 17 Oct hangs snowballed). The waiting recordings are retried with the usual backoff.
    Only cycles that needed Decider count towards the three: at the third such cycle in a row, one
    notice, "N items waiting on Decider", N being the recordings waiting; no other notice until
    Decider has answered again. Decider's router now raises DeciderUnavailable instead of returning
    an unrouted task, and no longer pauses itself for a minute; Jev's router (bench only) is
    unchanged. The `urgent` tag is ruling 12 (above). Inside a recording, a read that fails part way
    leaves the tasks already routed as Decider answered (a sensitive one holds the recording); only
    the tasks it did not reach are written urgent-unrouted. route() reports those as
    `Routed.unreached` and no longer raises.
30. **`--save-tasks` reads recordings already done again** (ruling 15's real-task set). The live
    run's recordings are `done` in the ledger, so a plain dry run would skip them as already
    processed and save nothing. Under `--save-tasks`, and only in a dry run, a `done` recording is
    taken through as if new; holds, `keep` and `error` stay as they are, and a dry run writes no
    ledger entry, no task and no notice (a test checks the ledger file is byte-identical after).
    The spend ledger is the real one, so the hard stop still counts these calls.
31. **The 19 Oct estimate.** `run_bench.py --estimate` now counts the second pass of `both` and
    the real tasks: the pass as approved is still at most 120 calls, **$0.12**; with `--option-descriptions
    both` and the 5 real tasks it is at most **202 calls, $0.21** (extraction is not repeated,
    only the routing questions). The two dry runs add at most 32 extraction calls (4 items, 2
    conditions, 4 attempts each). RESULTS.md's `[as measured]` label comes from a new
    `--as-measured NOTE` on `--render-from`.
32. **The 19 Oct dry runs listed Pocket over three days, not one.** `[poll] lookback_days = 1`
    asks Pocket for one day of recordings, and the first live run's recording of 17 Oct was outside
    it on the 19th: the first attempt saved the other two items and stopped before the pass, as
    built. The dry runs then ran with a scratchpad copy of `config.toml` (`RATATOSKR_CONFIG`)
    differing only in `lookback_days = 3`, with the same relative paths, so the same ledger and
    spend ledger; the repository's config is unchanged. In the first three-day attempt one
    recording's extraction failed the schema twice and was marked an error (in a dry run, written
    nowhere); the second attempt extracted all three, and the set was taken from it. The set is
    selected by recording id, so tonight's live-run recordings, which repeat the same sentences,
    are not in it.

## Bugs found and fixed during the build

- **The Telegram bot token could reach a log.** httpx logs every request's URL at INFO, and
  Telegram's URL contains the bot token. The CLI sets httpx to WARNING, so the service never
  logged it, but the protection depended on a log level: the test "no secret value reaches a log"
  passed in the full suite only because an earlier test had called the CLI's logging setup, and
  failed when its file ran alone. `notify.py` now adds a filter to the httpx and httpcore loggers
  that masks `/bot<token>` in every record at every level. A test runs httpx at DEBUG and finds
  only `/bot<redacted>`; a mutation removes the filter and the test goes red. The private
  repository has the same reliance on the log level (carry back, below). Every test file now also
  passes when run on its own.

- **The Strands hook demo's tool was never offered to the model.** In the first live hook check
  Strands logged "unrecognized tool specification": `add_task` was a plain function, and Strands
  needs the `@tool` decorator. Two of the three agent runs therefore never called it. The pattern's
  tests drove the hook directly, not through an `Agent`, so they could not see it. Fixed with
  `@tool`; a new test builds the real agent and checks that `add_task` is registered with its
  schema, and a mutation removes the decorator and the test goes red.
- **The bench reported Decider's version as "not installed in this venv"** (Decider lives in
  `.venv-decider`). It now reads the version from that venv's package metadata.

## Tests and the mutation check

- `pytest`: **789 passed, 1 skipped** (the real-recording shape test waits for Dan's device) in the
  working copy after ruling 28, and 788 passed, 2 skipped in a fresh clone of its commit, where the
  Hugging Face test is skipped (it needs `.venv-decider` and the weights) (781 and 1 in a fresh
  clone of the ruling 27 commit; 778 after ruling 24; 775 after ruling 23,
  with Dan's `media/` folder present; 764 and 1 after ruling 20;
  763 and 1 after rulings 13–15 and in a fresh clone of the re-measure commit; 739 and 1 at
  `475f532`; 722 and 1 overnight). No test touches the network, and every test file also passes
  on its own.
- `python tools/check_tests_bite.py`: **153/153 mutations caught**, the suite green again and the
  tree unchanged afterwards, after ruling 28 (152 after ruling 27; 149 after ruling 23; 144 after ruling 20; 143 after rulings 13–15, also in
  that fresh clone; 125 at `475f532`; 118 overnight). Each new mutation was made to fail on purpose
  once on its own before the full run.
- **The secret-string scan in the working copy (found 19 Oct, fixed by ruling 20).** It walked the
  working tree with a hand-kept list of folders to skip, not what `.gitignore` keeps out, so with
  Dan's `media/` folder present it failed on five untracked tool files (`.json`, `.csv`, holding
  40-character hashes) and read the git-ignored audio files as bytes to search them (nothing was
  played; only file names were reported). The committed tree passed in a fresh clone. It now scans
  what git could commit and nothing else (ruling 20).
- **Clean install from the repository alone:** fresh clone from GitHub, fresh Python 3.12 venv,
  `pip install -e ".[dev]"`, `pytest`, no `.env` present. Last done at `475f532` (under "Repository
  state").

## Bench

`bench/RESULTS.md` is the re-measure of rulings 15 and 17: the pass of 19 Oct 2026 (19:52–20:15
local; generated 00:15 UTC on 20 Oct) with `--option-descriptions both` and the real-task set,
then the rotation measured on its own with `bench/rotation_run.py` (20:15–20:58: 30 of 30 tasks,
every order answered, two scheduled restarts, both determinism checks 7 of 7), and RESULTS.md
re-rendered from the pass's saved state with that rotation, no model asked again; its hash check
passes. Conditions: another long-running Bedrock experiment on the same laptop paused, the laptop
otherwise idle, Decider at below-normal priority, 4 threads, 600 s per read. Worst case shown
before the pass: **$0.2074** (approved: $0.21). The pass of 17–18 Oct it replaces gave Decider the
same numbers, question by question, with the descriptions; Bedrock's and Jev's moved by a task or
two. Earlier runs: 17 Oct 00:03–02:03 (exposed two bench defects) and 02:30–04:49 (four questions);
two runs hung (hangs 2 and 3) and one starved (hang 4), none of them reported here.

**On the 30 canonical tasks**, with the descriptions (no extraction; agree / sure / agree when sure):

| | Decider | gpt-oss-20b (Bedrock) | Jev |
|---|---|---|---|
| gate (hold or not) | 30/30 | 27/29 | 29/30 |
| project | 24 / 18 / 18 of 18 | 24 / 28 / 24 of 28 | 27 / 26 / 25 of 26 |
| priority (set by the rule) | 16 / 12 / 9 of 12 | 22 / 28 / 21 of 28 | 20 / 22 / 18 of 22 |
| someone_waiting | 21 / 15 / 11 of 15 | 19 / 29 / 19 of 29 | 24 / 18 / 16 of 18 |
| hard_deadline | 12 / 21 / **9 of 21** | 24 / 29 / 24 of 29 | 25 / 25 / 22 of 25 |
| worth_researching_soon (10 labelled) | 4 / 3 / 1 of 3 | 5 / 8 / 5 of 8 | 7 / 2 / 1 of 2 |
| real_commitment | 29 / 25 / 25 of 25 | 28 / 29 / 28 of 29 | 28 / 26 / 26 of 26 |
| sensitive | 30 / 30 / 30 of 30 | 27 / 28 / 27 of 28 | 30 / 29 / 29 of 29 |
| median latency | 10.8 s | 1.7 s | 0.15 s |

**The source-text gate** asked Decider about each of the 12 transcripts that reached it (one
window each, 1.7–3.3 s): it held rec_014 (medical leave, p 0.85) and rec_015 (confidential
acquisition, p 0.76) before any Bedrock call and cleared the other ten (p 0.16–0.39): 12 of 12 as
labelled. **On the 16 tasks gpt-oss-20b extracted** from the other recordings (all 16 matched an
expected task): Decider's project 16 of 16 right (10 sure, all right), its post-extraction gate 16
of 16; Bedrock's project 15 of 16, Jev's 15 of 16. Median latency 10.9 s / 1.3 s / 0.18 s; cost
per task $0 / $0.000127 / $0.000048.

**With and without the options' descriptions** (ruling 15; the same tasks asked twice, the second
time with the project options as their names alone; right / sure):

| | with the descriptions | names alone |
|---|---|---|
| Decider, project, 30 canonical tasks | 24 / 18 | 22 / **4** |
| Decider, project, 16 extracted tasks | 16 / 10 | 10 / **2** |
| Decider, priority, canonical (right) | 16 | 10 |
| Bedrock, project, canonical | 24 / 28 | 22 / 30 |
| Jev, project, canonical | 27 / 26 | 22 / 24 |

Decider's other five questions gave the same numbers both ways; its priority fell because an
unsure project falls back to the unsorted default. Bedrock's other questions moved by up to three
tasks both ways, as its sampled answers do from run to run. Since 15(a) found the descriptions
already in place, this measures what they are worth: most of Decider's sureness about a project.

**The real tasks** (ruling 17): the first live run's two recordings that wrote tasks (`5541633a`,
recorded 17 Oct, and `8333490f`, 18 Oct) and the `/demo` sentence, extracted again by the
descriptions-on dry run: 2 + 2 + 1 = 5 tasks, as on 18 Oct. They are two commitments (sending
someone a draft paper, in three phrasings; an introduction, twice, word for word), so the sample
is very small. Decider was **not sure of the project for any of the five, either
way**: with the descriptions it leaned to `other` (p 0.19–0.24) four times and to `research` (p
0.30) once; without them to `work` or `writing` (p 0.30–0.49). In the courier all five go to
"unsorted", and both dry runs showed exactly that (`project=None` on every task). With the
descriptions Jev put all five in `research` (4 sure) and Bedrock four, the fifth in `community`
(all sure); without them Bedrock changed all five (two to `work`, two to `writing`, one to
`other`), and Jev moved three to `work`. Every other question gave the same answers both ways, except Bedrock's
`worth_researching_soon` (2 changed). RESULTS.md gives these as counts only; the task text stays
in the git-ignored `bench/raw/`. **This is the number the article leads with for real recordings**
(Dan's ruling 20): Decider sure of the project for 0 of 5 real tasks, with and without the
descriptions. The fixture numbers stand beside it, labelled as fixtures.

**Option order, three measurements** (7 orders over the 30 canonical tasks, Decider):

| | flips | first-listed option | wording of the project question | conditions |
|---|---|---|---|---|
| 19 Oct 20:15–20:58 | 2 of 30 (6.7%) | 29 of 210 (13.8%) | today's ("the owner's projects") | the other experiment paused, laptop idle |
| 18 Oct 00:03–01:57 | 2 of 30 (6.7%) | 29 of 210 (13.8%) | today's ("the owner's projects") | the other experiment paused, laptop idle |
| 17 Oct 04:49 | 3 of 30 (10.0%) | 29 of 210 (13.8%) | pre-7b ("Dan's projects") | the other experiment running |

The 19 Oct rotation repeats the 18 Oct one exactly: the same two tasks flipped, "Email Priya next
week" (work 5, other 2) and "Introduce Joaquim to Hedda Nyquist" (work 4, other 3), with the same
counts, which fits Decider's reads being deterministic. "Return the overdue library books"
(personal / other) flipped only on 17 Oct, with the older wording. None of the three runs is above
the 10% threshold, and the first-listed option was picked no more often than chance (14.3%).

**What the numbers say, plainly:** Decider's sure answers are right for the project (18 of 18),
real commitment (25 of 25) and sensitivity (30 of 30), and the source gate held both confidential
conversations upstream. The project descriptions are what make Decider sure of a project: without
them it was sure of 4 of 30. On the five real tasks it was sure of none, with or without them, so
in the courier they go to "unsorted": the small labelled fixtures overstate how often Decider
routes a real task. Ruling 7's `hard_deadline` is the weak question for Decider: it answered "no"
with confidence for 12 tasks labelled yes, among them stated near dates (on 18 Oct: "Email Priya
next week" 0.91, "before the next call" 0.90), so its floor does not protect the priority; Bedrock
and Jev mostly answered yes. Decider's `someone_waiting` was confidently "yes" on four tasks where
the labels say no, two of them where Dan is the one waiting (Tomasz's dataset, Mara's slides). As
a result Decider's priority agrees 16 of 30 against Bedrock's 22 and Jev's 20. Dan's ruling 8 (18
Oct): the wording stays as built; this is reported as measured, and RESULTS.md's "Sure and wrong"
section counts, question by question, every answer that was sure and wrong. gpt-oss-20b is still
sure of nearly every answer, right or wrong. Decider is the slowest of the three on this CPU and
the only one that costs nothing and never leaves the machine. The fixtures and the expected
answers are invented; the samples are small.

**The Strands hook, live** (`tools/hook_check.py`, 17 Oct 23:52 local, same server): the
confidential note (medical leave) was **held by the source gate before the agent ran** (sensitive
p 0.79), so nothing of it went to Bedrock, where on 17 Oct 04:49, gating only the tool's
arguments, it had gone through; the ordinary note's `add_task` call proceeded and the task was
added; the note naming the stop-listed client was held by the tripwire with no Decider read.
Strands logged one warning, "reasoningContent is not supported in multi-turn conversations with
the Chat Completions API"; the check completed.

## The first live run (Dan, 18 Oct 2026, 09:59–10:12)

On Dan's own Pocket, Todoist and Telegram, with the laptop otherwise idle, as Dan reported it:

- Strands Decider reads took **10.1–10.4 s**; the serve log confirmed 4 threads.
- Pocket listed 6 recordings; 2 drafts Dan had deleted were absent from the API's list.
- The tripwire held the real recording in which Pocket transcribed the stop-listed name as
  "Vargrime", as `stoplist:vargrheim` (ruling 6's spelling and sound match, in the wild).
- The source-text gate held the real medical-leave recording at p 0.43: **unsure**, so held.
- 5 tasks were written; the `/demo` round trip took about 60 s.
- Found: the tripwire's hold notice printed the recording's Pocket title, which Pocket builds
  from the conversation and which therefore carried the protected name; two accidental
  presses with no conversation reached the source gate (p 0.46) and were held with a notice each.
  Both are fixed by Dan's rulings 13 and 14 (above).

## The second live run (Dan, 19 Oct 2026, 19:20–19:33)

With the fixes of rulings 13–15 in place, the recordings repeating the first run's sentences, as
Dan reported it:

- Pocket was unreachable at startup; the courier treated it as transient and recovered on the next
  poll.
- One extraction failed the schema; the single retry succeeded.
- Strands Decider reads took **10.5–18.1 s**.
- 4 recordings: 3 tasks written, 2 holds (the tripwire's stop-list hold, and the source-text gate's
  unsure hold at p 0.44).

## Bedrock spend

From `ledger/spend.json` on 19 Oct after the re-measure: **$0.0650** over 542 calls (541 settled
at the reported usage, 1 kept at worst case: the deliberately invalid `reasoning_effort` probe),
515,865 input and 96,125 output tokens, no refusal. By purpose: routing in the benches $0.0425,
extraction (benches, dry runs and both live runs) $0.0173, structured-output probes $0.0042, the
hook checks $0.0004, a debugging call $0.0003, the reasoning-effort probe $0.0002, the smoke test
$0.00002. On 18 Oct after the overnight runs it stood at $0.0469. The hard stop ($5.00) was never
approached.

## Repository state

`github.com/dan-lee-odinson/ratatoskr-demo`, **private** (Dan makes it public on submission day),
default branch `main`, with the protect-main ruleset (deletion and force-push blocked on the default
branch; read back as exactly those two rules). On 18 Oct: `09790cd` (rulings 1–7b, 8 and 10, the
hang work, the overnight bench), `475f532` (rulings 9 and 12, the live run, the publish procedure),
`7e4d216` (rulings 13–15) and `2dad945` (rulings 16–17); on 19 Oct, the commit after them
(rulings 18–19, the media images, the re-measure). Clean install last verified on 18 Oct at
`7e4d216`, in the third publish test (fresh clone, fresh Python 3.12 venv, `pip install -e
".[dev]"`, no `.env`: `pytest` 763 passed, 1 skipped). No commit contains `.env`, `ledger/`, `processed.json` or
`bench/raw/`.

## Publish (submission day)

Dan's ruling 11: the private repository keeps its history; the public `ratatoskr-demo` is a new
repository whose history starts at `09790cd`, everything up to it squashed into one commit, and
every later commit replayed on top. Dan's ruling 16: `475f532` and `7e4d216` are replayed as
**one** commit (below, under "Tested"). Nothing here is run against a public remote until Dan says
so. In a shell, from a scratch folder (never the working copy):

```
git clone https://github.com/dan-lee-odinson/ratatoskr-demo.git ratatoskr-demo-publish
cd ratatoskr-demo-publish
git checkout --orphan public 09790cd
git commit -m "Initial import" -m "The public history starts here: the private development history up to and including 09790cd is squashed into this one commit." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git cherry-pick -n 475f532 7e4d216
{ git log --reverse --format=%B 09790cd..7e4d216 | grep -v '^Co-Authored-By:' | cat -s
  echo "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"; } > ../publish-msg.txt
git commit -F ../publish-msg.txt
git cherry-pick 7e4d216..origin/main      # only if there are commits after 7e4d216
git clone --single-branch --branch public "file://$PWD" ../ratatoskr-demo-public-check
cd ../ratatoskr-demo-public-check
git log -p --all | grep -ci -f <names-file>          # must print 0
```

`<names-file>` lists, one per line, the names ruling 10 keeps out of the public repository. It
lives outside the repository (Dan's copy: `publish-forbidden-names.txt` in his ratatoskr folder),
so that this check does not itself put the names into the history.

Then the clean-clone check from `ratatoskr-demo-public-check`: a fresh Python 3.12 venv,
`pip install -e ".[dev]"`, `pytest`, with no `.env`; and the license placeholders (ruling 27), which
must return nothing:

```
grep -rn "\[yyyy\]\|\[name of copyright owner\]" --include="*" .
```

Only after that, and on Dan's word:

```
gh repo rename ratatoskr-demo-private --repo dan-lee-odinson/ratatoskr-demo --yes
gh repo create dan-lee-odinson/ratatoskr-demo --public --description "Gatekeeper triage for a voice-to-task courier, with Strands Decider"
git -C ratatoskr-demo-public-check push https://github.com/dan-lee-odinson/ratatoskr-demo.git public:main
```

followed by the protect-main ruleset on the new repository (deletion and force-push of the default
branch blocked, read back), and, in the working copy,
`git remote set-url origin https://github.com/dan-lee-odinson/ratatoskr-demo-private.git`. Renaming
first frees the name; GitHub's redirect from the old name to the renamed private repository stops
once the new public repository takes the name.

**Tested 18 Oct 2026 on a throwaway clone of the private repository**, three times. First, before any
later commit: the orphan root's tree is identical to `09790cd`'s; the clone of the `public` branch
alone has 1 commit, `git log -p --all` finds none of the names (the private history has 4), and
the old commits' objects (`021d735`, `8bd8b47`) are absent. Second, after `475f532` (rulings 8–12,
the live run): the cherry-pick of `09790cd..origin/main` (1 commit) applied cleanly, the
`public` branch's tree is identical to `origin/main`'s, the single-branch clone has 2 commits, and
the clean-clone check passed (fresh Python 3.12 venv, `pip install -e ".[dev]"`, no `.env`:
`pytest` 739 passed, 1 skipped). **But the names check found 3**, all in `475f532` itself: this
section then quoted the names in its own grep commands and in the sentence above. The commit
after it removes them, but a commit that removes a line shows it in `git log -p` too, so the
public replay must not carry `475f532` and its fix as two commits. Third, after `7e4d216` (the
fix), with the two replayed as one commit as above: the root's tree is `09790cd`'s, the `public`
branch's tree is identical to `origin/main`'s, the single-branch clone has 2 commits, the names
check prints **0**, and the clean-clone check passed (`pytest` 763 passed, 1 skipped). Dan
approved this replay (ruling 16); the private history is not touched.

## Carry back to the private repository for v1.2

1. The transcript shape, once `--inspect-shape` has run on a real recording.
2. Strands Decider as a router: local, no per-call cost, the six questions in one read, and the
   priority rule (three Nouls, the due date and code instead of a Score), with `due.py`.
3. The source-text gate, if the private courier gets a local Decider: the only check that sees a
   detail before the extraction drops it.
4. The input-adapter registry (`[devices]`), so the courier also runs without a wearable.
5. **The stop list's spellings and sound match** (Dan's ruling 6): the private stop list's entries
   become `{term, spellings}`, with the transcriber's misspellings of the real client name listed,
   and `triage.stop_list_hit()` with its Double Metaphone branch and the neighbouring-word join
   (`Metaphone==0.6`), with its tests and mutations. Pocket has already misspelled a stop-listed
   name once.
6. The device envelope and the wrist adapter, if the watch is wanted there too.
7. The hermetic test guard (no real secrets file, no network), and the extended C6 grep.
8. The bot-token log filter (`notify.py`): the private courier also relies on httpx being kept at
   WARNING to keep the token out of the journal.
9. If the private courier ever moves to an open-weight model on Bedrock: mantle's
   `response_format` did not enforce the schema for gpt-oss-20b; use JSON in text.

## Still to do (Dan)

1. On submission day: publish as under "Publish (submission day)" (rulings 11 and 16), on Dan's
   word.

The re-measure of rulings 15 and 17 ran on 19 Oct (under "Bench"; decision 32): the two dry runs
(`--once --dry-run --save-tasks --demo-text`, descriptions on and off), the estimate, the pass
(`--rotations 0 --option-descriptions both --real-tasks`), Decider stopped by its PID, then
`bench/rotation_run.py`, and RESULTS.md re-rendered with that rotation.

## Dan's rulings, 21 October 2026

Rulings 29 to 41 are recorded in the private development history only; these three entries were
added by ruling 49. Each gives its commits in both histories, the private one first.

42. **The walkthrough link.** The README gained the walkthrough line ("Walkthrough (2.5 min, screen
    capture of the courier running)") under the demo video line, with a blank line between them so
    the two render as separate paragraphs. The same sentence, linked, ends the notebook's first
    cell (source only; outputs as committed), and the HTML export was regenerated, since
    `notebooks/README.md` links to it. `media/walkthrough/` is ignored; nothing from it was ever
    committed to either repository. Private `42e47bf`, public `667c93e`: four files, blob for blob,
    after the secret scan (168 files, clean) and the forbidden-names grep (0). No code changed: the
    suite stood at 812 passed, 1 skipped, and the mutation check was not re-run.
43. **Unsorted tasks resolved from the wrist, and the fresh evaluation's replay** (ruling 44 folded
    in). The resolution loop: an unsorted task's wrist message carries one button per project, in
    the order of Decider's own probabilities for that task, and Leave unsorted. A tap adds the
    `proj:` label to the existing Todoist task (`item_update`, labels only: the labels the courier
    wrote plus `proj:<key>`, since Todoist replaces the whole list) and rewrites the message's first
    line; Leave changes nothing in Todoist. Each pick is kept in its recording's ledger entry under
    `picks`, keyed by a ten-character short id (callback data `pick:<id>:<key>`, under 64 bytes),
    with who picked, the key and the time, and no task text. A repeat tap, an unknown task, another
    chat and a Todoist refusal (buttons kept, ledger unwritten) each have their answer, and no model
    is asked. Only tasks Decider answered and was unsure of get buttons. The Telegram receiver is a
    thread of `--loop`, so taps are answered while it runs and not during `--once`.
    `ratatoskr_demo/resolve.py`, `tests/test_resolve.py` (18), nine mutations. The replay:
    `bench/fresh_eval_2026-10-22.json` is the fresh evaluation as it recorded it, and what the run
    did not record (each gate window's probability, the courier's own answers about the tasks it
    extracted, per-project probabilities) is named as not recorded; `python bench/replay_fresh.py
    --check` regenerates `bench/FRESH_EVAL_2026-10-22.md` from it byte for byte with no network,
    model or key, and the suite runs it with the network refused and no `.env`. The notebook's
    section 9 gained a last cell that reproduces the fresh table from the export. Tests (4), two
    mutations. Folded in from ruling 44: the pattern README's question count (six, not five); the
    README's Decider memory line matched to System requirements ("16 GB minimum host RAM"); the
    notebook's priority explanation (a sure near deadline still sets high when waiting is unsure);
    the note beside the live-versus-recorded comparison. Private `a407834` and `09a8ed6`, public
    `f0659c5` and `bc82e5f`. The public clone of `bc82e5f`: 832 passed, 3 skipped, and 176 of 176
    mutations caught; the private working copy: 834 passed, 1 skipped, and 176 of 176.
47. **Photos: the image gate and the image evaluation** (ruling 47B). The glasses input reads photos
    (PNG, JPEG, WebP or GIF) dropped in `ledger/inbox/photos/`, and each passes three checks on the
    laptop before any cloud call. A missing, undecodable, unsupported or blank file (grayscale
    standard deviation under 2 of 255) is held as `unreadable` with no model asked. The image gate,
    a second Strands Decider server (strands-decider 0.2.0 with `--vision` in its own venv, the same
    checkpoint, 127.0.0.1:8001, started by `tools/serve_vision.py`), asks "Is this picture of
    something that must never leave this machine?" with the text gate's criteria at its floor (0.60;
    unsure from 0.40) and holds on yes or unsure. Then RapidOCR (rapidocr-onnxruntime 1.4.4, the
    `photos` extra) reads the picture's text on the laptop, and the existing path runs unchanged:
    the tripwire, the text gate, extraction on gpt-oss-20b, routing, the rule, Todoist, Telegram.
    The ledger keeps the image gate's probability and read time, the OCR's per-line confidence and
    the extraction call count; the OCR's text is never kept. No Bedrock vision model is used (a v2
    item). The checkpoint was not trained on images: the image gate uses the vision tower inside its
    base model. Tests (28), ten mutations. The evaluation: sixteen rendered pictures, eight ordinary
    and eight that must not leave the machine, `bench/images/labels.json` sha256 `7ae6ef6bc5d041ba
    feecdca72133add6 0e55c668173d1a86 545dd8381d66b2f0`, committed before the first model call; run
    21 Oct 19:32-19:34 UTC through the courier's own cycle, both Decider servers fresh and stopped by
    their PIDs. No sensitive picture was cleared: seven were held by the image gate as sensitive and
    one, the counselling card at p 0.59, as unsure. Three of the eight ordinary pictures were held
    by the image gate as unsure (p 0.40, 0.42 and 0.43), and the eleven held pictures had no
    extraction call and no write. On the five cleared, the OCR recovered every key word; the
    extraction was right on three and missed on two (the conference slide and the flyer: the text
    reached the model, which found no commitment, and the courier wrote its contact note). Of the
    four matched tasks, one was filed to the right project and three left unsorted, none wrong.
    Median and p90 reads: image gate 2.83 and 2.96 s, OCR 1.51 and 2.99 s, text gate 1.72 and
    2.00 s, routing 12.47 and 13.34 s. Bedrock: $0.000507 over 5 calls. The highest ordinary picture
    scored 0.43 and the lowest sensitive one 0.59; the floor stays the text gate's.
    `python bench/run_images.py --check` regenerates `bench/IMAGE_EVAL_2026-10-22.md` from
    `bench/image_eval_2026-10-22.json` byte for byte with no network, model or key. Private
    `6fc60f2` and `d15d29c`, public `544dab0` and `79c559a`. The public clone of `79c559a`: 860
    passed, 3 skipped, and 186 of 186 mutations caught; the private working copy: 862 passed,
    1 skipped, and 186 of 186. After it, ruling 49: a photo held by the tripwire, the text gate,
    the check after extraction or the leak guard is told `Held <id>: <reason> (photo). It stays in
    the inbox folder.` instead of the Pocket release instruction, and a recording's notice is
    unchanged; there is no release for photos yet, and the README's Photos paragraph says a held
    photo stays in the folder. Four tests, one per notice site; two mutations.
