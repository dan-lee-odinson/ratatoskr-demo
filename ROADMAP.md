# Ratatoskr Demo: development roadmap

This is a plan, not a progress report. Everything below is **Planned** unless the baseline table says it is built.
A horizon (Now, Next, Later, Exploratory) is a priority, never a sign that work has begun or a promise of delivery.
There are no dates, and none will be published until real estimates exist. Every target here is a **proposed
target**, declared before the run that tests it; no accuracy is projected.

The entry: [*Ratatoskr: A Smart Wearable Dispatcher, Powered by Strands Decider 2B*](https://builder.aws.com/project/3KyHho3TSCx8dWc3u14ypmOfgXs)
on AWS Builder Center. Demo video: https://youtu.be/dl9dS00GEFY. Walkthrough: https://youtu.be/RGEm62mWekI.
Tracking: the public board [Ratatoskr Development Roadmap](https://github.com/users/dan-lee-odinson/projects/3), with one milestone and one parent issue
per workstream ([Tracking](#tracking)).

## Direction

Ratatoskr makes uncertainty actionable while the owner keeps authority:

1. Private source material is checked on the laptop before any generative model reads it.
2. Strands Decider returns typed judgments with probabilities; application code enforces the policy.
3. An abstention can become an explicit owner decision, recorded apart from the model's answer.
4. The same rules extend across speech, pictures and, later, wearable input.
5. Owner corrections can supply approved local context without silently changing policy.

The design rests on a finding already in this repository: an extracted tool argument can look harmless once the
private context around it has been dropped. On 17 October 2026 a Strands hook judging tool arguments alone let such a
note through, so the courier gates the source text before Bedrock and checks the proposed tasks before any write
([where the gate looks, and why](patterns/gatekeeper_triage/README.md#where-the-gate-looks-and-why)).

**The goal.** Each iteration seeks to reduce the Decider loop's resource use, with measured quality and reliability
constraints, so that it can run always-on and, in the end, on hardware a maker can build. Negative and mixed results
are published; a candidate need not improve every dimension.

## Where it stands

| capability | state | evidence |
|---|---|---|
| The text courier: a conversation from Pocket or Telegram's `/demo` through the name tripwire, Decider's source-text gate, extraction by `openai.gpt-oss-20b` on Bedrock, six typed judgments per task, the sensitivity hold, then Todoist, Telegram and the watch's three-line notice | implemented | [README](README.md), [bench/RESULTS.md](bench/RESULTS.md) |
| The fresh evaluation: 24 fictional conversations through the courier's own cycle, labels committed and hashed before the first call, every table replayable without a model | implemented, measured | [bench/FRESH_EVAL_2026-10-22.md](bench/FRESH_EVAL_2026-10-22.md), tag [`eval-freeze-2026-10-22`](https://github.com/dan-lee-odinson/ratatoskr-demo/tree/eval-freeze-2026-10-22) |
| Owner filing of an unsorted task: one Telegram button per project and `Leave unsorted`, recorded as the owner's | implemented for polled recordings and photos; not for `/demo` text (M1) | [README, Unsorted tasks](README.md#unsorted-tasks); clip https://youtu.be/-QixQrUyyws |
| Photo input from a folder: the readability check, the image gate on a vision server, RapidOCR, the tripwire and text gate, the commitment-or-reference scene, owner Release, reference notes kept on the laptop | implemented; evaluated on rendered pictures and on twelve phone photographs | [first run](bench/IMAGE_EVAL_2026-10-21.md), [second run](bench/IMAGE_EVAL_2026-10-21_scene.md), [released pictures](bench/IMAGE_EVAL_2026-10-22_released.md), [second addendum](bench/IMAGE_EVAL_2026-10-22_released2.md), [final run](bench/IMAGE_EVAL_2026-10-22_final.md), [camera photographs](bench/IMAGE_EVAL_2026-10-22_camera.md); clip https://youtu.be/pDflGWOV-xY |
| Glasses capture | planned (M4); photos reach the courier through the folder `ledger/inbox/photos/` | [README, Photos](README.md#photos) |
| The watch | implemented as a display (the Telegram notice); as an input, planned and exploratory until its route check (M4) | [README](README.md) |
| GPU serving (optional, bfloat16 weights), measured against the CPU path | implemented, measured | [RESULTS.md, Server variants](bench/RESULTS.md#server-variants-ruling-48-21-oct-2026) |
| The companion app | prototype: a mobile web page at phone width in a desktop browser; the private viewer is simulated; category filing reaches Todoist through the courier's owner service; tags, Keep held, Release and scene answers stay in the page | [app/README.md](app/README.md); commits [`3e582c0`](https://github.com/dan-lee-odinson/ratatoskr-demo/commit/3e582c0), [`55283d3`](https://github.com/dan-lee-odinson/ratatoskr-demo/commit/55283d3), [`a573c40`](https://github.com/dan-lee-odinson/ratatoskr-demo/commit/a573c40); recording https://youtu.be/BLX68APO6LM |
| Held content on the owner's phone | planned (M3) | - |
| The notebook: the text gatekeeper, recorded or live, no Bedrock call | implemented; its rendered export is that of the live run of 21 October 2026, linked at its version; refreshing it from a new live run is planned (Now) | [notebooks/gatekeeper_triage.ipynb](notebooks/gatekeeper_triage.ipynb); [the export at `b05d795`](https://github.com/dan-lee-odinson/ratatoskr-demo/blob/b05d795/notebooks/gatekeeper_triage.html) |
| The reusable recipes | implemented | [patterns/gatekeeper_triage/](patterns/gatekeeper_triage/README.md), [patterns/photo_scene/](patterns/photo_scene/README.md) |
| An always-on Hub | exploratory (M5) | - |
| The next checkpoint | planned (R1) | - |

## Measured limitations

The figures as the reports state them.

- **Photographed commitments: 0 of 4 written as their task** ([camera report](bench/IMAGE_EVAL_2026-10-22_camera.md)).
  Two causes, kept apart. On c01 the owner's scene answer was Reference, a mis-tap recorded after the run (Commitment
  was intended), so the card was kept as reference and nothing was extracted. On c02, c03 and c06 the OCR missed key
  words and the extraction found no commitment; what was written was a contact note with no follow-ups, which is not
  the task. The OCR missed a key word on every handwritten card it read (8 of 8). In the same run none of the four
  sensitive cards reached Bedrock or Todoist, and all four reference cards were kept on the laptop with no owner action.
- **Project routing on labeled task text: 8 of 26** ([fresh report](bench/FRESH_EVAL_2026-10-22.md)). Decider's
  project answer about each of the 26 labeled ordinary tasks as written, with no extraction in between: sure and right
  on 8, sure and wrong on 0, an abstention (left unsorted) on 18. Priority was right on 11 of 26. These are routing on
  labeled text, not the courier's end-to-end result; what the courier wrote is the report's own table. In the same
  run 0 of 12 sensitive conversations were written, 3 of 12 ordinary conversations were held by the source gate and 1
  by the task check.
- **Waiting and deadline** ([RESULTS.md, canonical tasks](bench/RESULTS.md#agreement-on-the-canonical-tasks-no-extraction)).
  On the 30 canonical tasks as written: `someone_waiting` sure on 15 of 30 and right on 12 of those 15;
  `hard_deadline` sure on 21 of 30 and right on 16 of those 21.
- **Resources on the CPU** ([Server variants](bench/RESULTS.md#server-variants-ruling-48-21-oct-2026)). Warmed, a
  six-question read takes 11.01 s median and a gate window 2.23 s; the server holds 9.94 GB of private host memory at
  load and 10.56 GB at peak. The courier runs only while the laptop does.

## Horizons and dependencies

Every item starts at status Planned.

| horizon | items |
|---|---|
| Now | `/demo` filing buttons (M1); refresh the notebook's rendered export from a live run (housekeeping); measure the next checkpoint (R1); the companion's remaining controls through the owner service (M3) |
| Next | approved example tasks with their approval rule (M1); the handwriting reader and its camera evaluation (M2); the home-network held-content path (M3); the native Android companion with glasses capture, and the Artemis firmware v1, one milestone (M4) |
| Later | the CPU variants (M5 stage one, validated under R1); the GPU kernels (R1); the overlay extension (M3) |
| Exploratory | cross-capture promise tracking (M1); the Hub's stages two and three (M5) |

Only the real dependencies:

- Held-content viewing needs the authenticated service contract (both M3).
- The Android companion's private-content and action integration needs the specific M3 contract it uses; its UI
  scaffolding, discovery and glasses capture do not wait for M3. The watch's actions need the contract they use, which
  the route check names.
- The CPU variants follow the next-checkpoint measurement. The photo path moves to a new checkpoint only after R1's
  comparison.
- Each Hub stage follows the gate of the one before it; a failed gate advances nothing.

## Workstreams

### M1 — Project context and owner-assisted completion

**Status / horizon:** Planned. `/demo` filing buttons: Now. Approved example tasks and their evaluation: Next.
Cross-capture promise tracking: Exploratory.

**Observed limitation:** the project descriptions carry the confidence: Decider was sure of the project on 18 of the
30 canonical tasks with each project's one-line description and on 4 of 30 with the names alone
([RESULTS.md](bench/RESULTS.md#with-and-without-the-project-options-descriptions-the-canonical-tasks)). On fresh
labeled task text it filed 8 of 26 automatically and abstained on 18. A `/demo` task Decider is unsure of is written
unsorted without the filing buttons a recording gets. Priority, right on 11 of 26, is a separate problem and stays
visible as one.

**User outcome:** fewer tasks left unsorted; when Decider is unsure, one tap files the task, however the conversation
arrived; the owner's filings can become approved context for the next ones.

**Decider's decision:** the existing typed project choice, given better approved context: each project's line plus two
or three approved example tasks.

**What application code / Bedrock / the owner controls:** code holds the examples, turns each tap into a proposal, and
keeps approval, provenance and removal; adding an example changes no floor and no rule. Bedrock extracts, as today.
The owner decides which proposals become examples, and removes any.

**Work packages:**

1. `/demo` text gets the same filing buttons as a recording (Now).
2. Approved example tasks: two or three per project beside its description. Every tap on the filing buttons proposes
   one; a proposal becomes an example only on the owner's explicit approval, keeps its provenance (which task, which
   tap, when), and can be removed. A tap is never taken as ground truth on its own (Next).
3. The owner-assisted path evaluated on a new labeled set (Next).

One exploratory line, no more: cross-capture promise tracking, recognizing one promise seen in a recording and again on
a card.

**Dependencies:** 3 needs 2. Nothing outside M1.

**Completion criteria (proposed):**

- A `/demo` task Decider is unsure of arrives with the filing buttons; a tap files it once, recorded as the owner's; a
  second tap changes nothing.
- Approved examples enter the routing state and can be removed without changing any floor or rule; a tap alone never
  adds one; each example keeps its provenance across a restart.
- A frozen comparison on a new labeled set reports automatic precision and coverage, correctness after the owner's
  filing, the taps per task and any duplicate write, against the current configuration, with its targets declared
  before the run. Priority is reported on its own; better project context is not presented as better priority.

**Evidence to publish:** the configuration diff, focused tests of the proposal, approval, provenance and removal, the new
report with its replayable export, and one traced end-to-end case.

**Risks or unresolved choices:** where examples are kept and where a proposal is shown for approval (Telegram, the
companion, or both); whether approved examples move Decider's confidence across the 0.70 floor at all (the evaluation
answers it); the evaluation set must be new, or the examples are tested on what they were drawn from.

**Tracking:** [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/1) · [parent issue](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/1) · sub-issues
[M1.1](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/2), [M1.2](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/3), [M1.3](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/4)

### M2 — Photographed commitments that survive real input

**Status / horizon:** Planned. Next.

**Observed limitation:** the camera run wrote 0 of 4 commitments as their task; the OCR missed a key word on 8 of the 8
handwritten cards it read ([camera report](bench/IMAGE_EVAL_2026-10-22_camera.md); the owner's mis-tap on c01 is
counted apart).

**User outcome:** a handwritten note photographed on a phone becomes the right task, while sensitive cards stay held and
reference material stays on the laptop.

**Decider's decision:** unchanged: the image gate, the text gate and the commitment-or-reference scene. Reading the
handwriting is not a Decider question.

**What application code / Bedrock / the owner controls:** code places the reader after the image gate (its text meets the
tripwire and the text gate as the OCR's does) and dispatches as today. Bedrock extracts; if no local reader is good
enough, a Bedrock vision model reads the picture after the gate, named only once its availability and image input are
verified. The owner releases and answers the scene, as today.

**Work packages:**

1. The handwriting reader: a local one if a small enough model reads handwriting, otherwise a Bedrock vision model
   after the gate; evaluated through the existing folder input, without waiting for native capture (Next).
2. A new camera evaluation: new handmade cards, labels committed and hashed before the first call, and a target
   declared before the run. The camera report's 0 of 4 stays the first report and the baseline (Next).
3. The photo path's vision server on a new checkpoint, only if R1's comparison adopts one.

Glasses capture has one owner, M4 ([M4.2](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/16)); M2 does not wait for it.

**Dependencies:** 2 needs 1; 3 follows R1's next-checkpoint comparison. A reader that is a new checkpoint is validated
under R1's discipline.

**Completion criteria (proposed):**

- The report matches each intended task against what was extracted: who owes whom, what, the date, and the final
  disposition. A contact note or a completed API call is not a task.
- Automatic and owner-assisted results, reference retention and sensitive holds are counted with separate
  denominators; an owner text correction or scene answer counts as assistance.
- No sensitive card reaches Bedrock or Todoist and no write repeats in the declared cases; latency, memory and cloud
  calls are recorded.
- A minimum recovery target is declared before the run; if it is not met, the reader stays experimental and the result
  is published.

**Evidence to publish:** the reader comparison with its measurements, the new report with its replayable export, and the
first camera report unchanged.

**Risks or unresolved choices:** a reader small enough for the laptop may not read handwriting well enough; a Bedrock
reader would send the picture itself, not only its text, to Bedrock after the gate, and the README's privacy section
would have to say so before it is used.

**Tracking:** [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/2) · [parent issue](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/5) · sub-issues
[M2.1](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/6), [M2.2](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/7), [M2.3](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/8)

### M3 — Secure owner review with real local content

**Status / horizon:** Planned. The companion's remaining controls: Now. The home-network held-content path: Next. The
overlay extension: Later.

**Observed limitation:** in the companion prototype the private viewer is simulated, and tags, Keep held, Release and
scene answers stay in the page; only category filing reaches the courier, through an owner service on the laptop's
loopback address ([app/README.md](app/README.md)).

**User outcome:** the owner reviews a held item's real content on their own phone, and every control in the companion
acts on the courier, acknowledged only once the courier has applied it.

**Decider's decision:** which content is held or unsure, as today. Authentication, authorization, transport protection
and expiry belong to code.

**What application code / Bedrock / the owner controls:** code holds the service contract, the authentication, the
certificate check, the state transitions and their acknowledgment. Bedrock sees no held content. The owner makes every
release, scene answer, category and tag.

**The connection, as decided.** The owner service bound to the laptop's home-network address, with a bearer token and a
self-signed certificate that the client validates against that specific pinned certificate and service identity: no
trust-all handlers, no disabled checks, no browser-warning bypass, and the browser prototype's path verified separately
from Android's trust-anchor configuration. Held content leaves the laptop only to the owner's phone on that path. The
later away-from-home extension is a private overlay network between phone and laptop (a WireGuard-style link, the same
service bound to the overlay address); it blocks no home-network work. Considered and rejected for held content: a
provider-terminated HTTPS tunnel to a public address, because the provider can decrypt the content (end-to-end tunnel
configurations exist and were not chosen). A client of the owner's Telegram account is out. QR pairing is not the
method: the owner enters the token and the certificate's SHA-256 fingerprint, shown on the laptop, into the client, as
the browser prototype takes the token today. The loopback service is never exposed as it is.

**Work packages,** in Dan's order of delivery:

1. The companion's remaining controls through the owner service: tags written to Todoist as labels; Release and scene
   answers through the same handlers as the Telegram buttons; each recorded `via: app` (Now).
2. The authenticated service contract on the home network, as above (Next).
3. Held-content viewing on the owner's phone over that contract (Next).
4. The overlay extension for away from home (Later).

**Dependencies:** the strict technical prerequisite for held-content viewing (3) is the authenticated service contract
(2). Delivering 1 first is the order of delivery, not a dependency. 4 blocks nothing on the home network.

**Completion criteria (proposed):**

- An authorized owner views a fictional held item through the real local-content path; an unauthorized, expired or
  cross-item request fails; a notification never carries the held payload.
- Each client refuses a server whose certificate or identity does not match the pinned one.
- Test instrumentation shows no held payload entering cloud processing on the tested paths: an observed test condition,
  not a universal privacy guarantee.
- An owner action is acknowledged only after the courier's state transition succeeds; a retry creates no duplicate.
- The companion keeps local prototype edits and acknowledged courier changes visibly apart throughout.

**Evidence to publish:** a data-flow diagram, focused positive and negative integration checks, and a labeled
demonstration.

**Risks or unresolved choices:** expiry and replay windows; what the companion shows when the laptop is asleep or off the
network.

**Tracking:** [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/3) · [parent issue](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/9) · sub-issues
[M3.1](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/10), [M3.2](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/11), [M3.3](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/12), [M3.4](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/13)

### M4 — The companion app and wearable input

**Status / horizon:** Planned. Next: one milestone with two parts, the native Android companion first, the watch
firmware beside it.

**Observed limitation:** the companion is a mobile web prototype; the watch is a display; photos arrive through a folder.

**User outcome:** a native Android companion for every user, which also captures from the glasses; for those who have
the watch, the filing buttons and *hold* on the wrist.

**Decider's decision:** unchanged. A new device is an input; Decider does not run on it.

**What application code / Bedrock / the owner controls:** code keeps each device envelope's identity, offline and retry
handling, and duplicate protection. Bedrock is unchanged. The owner acts from the phone or the wrist.

**Part one: the native Android companion.** The client of M3's contract: the review queue, the categories and tags, the
held-content path. The canonical owner of glasses capture, through Meta's
[Wearables Device Access Toolkit](https://developers.meta.com/wearables/) (its
[Android SDK](https://github.com/facebook/meta-wearables-dat-android) is in developer preview) and its mock device kit;
demonstrated in the emulator or the mock kit if the glasses are not at hand, and labeled as such. Its UI scaffolding
and discovery proceed on their own; only its private-content and action integration waits, and only for the specific
M3 contract it needs.

**Part two: the Artemis smartwatch firmware v1.** The watch as an input, with the filing buttons and *hold* on the
wrist; exploratory until its route check answers three questions:

1. Can the stock notification path carry a tap back?
2. Can CircuitMess Connect relay an owner action on Android?
3. Can an app run inside the stock firmware's structure?

Two published starting points. A fork of the stock firmware,
[CircuitMess/GC_Artemis-Firmware](https://github.com/CircuitMess/GC_Artemis-Firmware): C++ on ESP-IDF 5.1, itself a
fork of CircuitMess's Clockstar firmware; release v2.0 of 30 July 2025 added support for hardware revision 2.0, and
v2.2 of 10 August 2026 lists "New Bluetooth functionality support"; prebuilt binaries are on its
[releases page](https://github.com/CircuitMess/GC_Artemis-Firmware/releases). Or an app through CircuitMess's coding
hub, [code.circuitmess.com](https://code.circuitmess.com) (CircuitBlocks; sign-in required), with
[CircuitMess/micropython](https://github.com/CircuitMess/micropython), their MicroPython for CircuitMess devices. A
feasibility reference, not proof of an Android integration:
[Reidond/artemis-watch-firmware](https://github.com/Reidond/artemis-watch-firmware), a GPL-3.0 ESP-IDF 5.5 / LVGL
firmware for the Artemis Watch 2.0 with iPhone notifications and a companion app over BLE. The hardware
(ESP32-S3-MINI-1, Wi-Fi 4 and Bluetooth 5.0, 600 mAh) allows either the watch reaching the owner service on its own
Wi-Fi or the phone relaying over BLE; which one is the route check's answer.

**Work packages:**

1. The Android companion: UI scaffolding and discovery (proceeds on its own).
2. Glasses capture through the toolkit and its mock device kit (the canonical capture task; M2 links it).
3. The Android companion's private-content and action integration (needs M3's service contract).
4. The Artemis firmware v1: the route check, then the wrist actions.

**Dependencies:** 3 needs M3's authenticated service contract ([M3.2](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/11)). The wrist actions need the
contract they use, named by the route check. 1, 2 and the route check do not wait for M3.

**Completion criteria (proposed):**

- A captured fictional input reaches the laptop with its device identity and is disposed of exactly once.
- The app receives review requests and acknowledged filing updates.
- A network interruption or a repeated delivery duplicates no task and no owner decision.
- The evidence states which parts used the emulator, the mock device kit, a real phone, the glasses or the watch.
- The route check's three answers are published with the route chosen, before any wrist action is built.

**Evidence to publish:** labeled recordings of each part, the device-envelope tests, and the route check's findings.

**Risks or unresolved choices:** the toolkit is a developer preview, and its standalone camera capture is marked
experimental; the stock firmware may offer no route back from the wrist; reflashing the watch needs its recovery path
documented before the first flash.

**Tracking:** [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/4) · [parent issue](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/14) · sub-issues
[M4.1](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/15), [M4.2](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/16), [M4.3](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/17), [M4.4](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/18)

### M5 — The Hub

**Status / horizon:** Planned; exploratory overall. Stage one: Later. Stages two and three: Exploratory.

**Observed limitation:** the Decider loop runs on the laptop, holding about 10 GB of private memory on the CPU and taking
about 11 s per six-question read ([Server variants](bench/RESULTS.md#server-variants-ruling-48-21-oct-2026)); the
courier is up only while the laptop is.

**User outcome:** an always-on courier for text on a dedicated box and, in the end, a box a maker can build.

**Decider's decision:** unchanged: the same questions and floors. Any changed configuration is validated under R1 before
it is adopted.

**What application code / Bedrock / the owner controls:** code moves, not policy: the same rules on another host. Bedrock
extracts, as today. The owner's actions are recorded by the Hub.

**Three stages, each with a predeclared gate** ([evaluation policy](#stage-gates)):

1. **Stage one (Later):** the CPU variants on the laptop: lower precision, quantized weights, a different runtime if the
   checkpoint's architecture is supported there. This is the one canonical issue for the CPU variants: R1 owns their
   validation discipline, M5 their deployment outcome.
2. **Stage two (Exploratory):** an intermediate always-on box of mini-PC class running the complete text courier and the
   text server, with the one requirement its power supply imposes: a UPS and a clean shutdown.
3. **Stage three (Exploratory):** the maker build, a Raspberry Pi-class board or similar, with a parts list, a wiring
   diagram, a flashable image with its provenance, recovery instructions, and the one configuration measured on that
   board.

**The laptop / Hub split for photos.** The laptop keeps the photo bytes and runs the readability check, the image gate on
the vision server, and the OCR, then sends the post-gate text with an opaque photo ID to the Hub over an
authenticated, encrypted device-to-device link. The Hub runs the tripwire and the text gate, then asks the laptop's
vision service for the scene decision by that ID (the picture and its text together, on the laptop, as today), records
the answer, holds or asks the owner as the rules say, and continues the permitted path to extraction and routing. The
Hub is the single authority for the ledger, held-item state, owner actions and reference notes. The laptop's vision
service is a named responsibility with its own unavailable-laptop behavior: the photo waits, as a recording waits when
Decider is away. Photo bytes never leave the laptop for the Hub. The Hub's always-on text operation is distinct from
photo operation, which still needs the laptop; a measurement of the Hub alone is reported as the text Hub only. This
handoff is a proposed implementation choice, documented and tested before the split is built; a text-only substitute
for the scene question would be a different decision configuration needing its own evaluation.

**Off this path:** the GPU kernels are a laptop optimization (R1).

**Work packages:** the three stages above, and the split's design and tests.

**Dependencies:** stage one follows R1's next-checkpoint measurement; stage two follows stage one's gate, stage three
stage two's; a failed gate advances nothing. The split is designed and tested before any photo operation on the Hub.

**Completion criteria:** each stage's gate ([stage gates](#stage-gates)).

**Evidence to publish:** each stage's threshold declaration before its acceptance run, then the run's report with its
four measurement families, failures and tradeoffs included; for stage three, the parts list, wiring, image provenance
and recovery instructions as tested.

**Risks or unresolved choices:** the checkpoint's architecture may not be supported by the runtimes that make CPU
inference lighter; lower precision or quantization may change decisions, which is R1's to validate; a maker board may
not hold the model in memory, in which case stage three is published as not passed.

**Tracking:** [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/5) · [parent issue](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/19) · sub-issues
[M5.1](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/20), [M5.2](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/21), [M5.3](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/22), [M5.4](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/23)

### R1 — Checkpoint and confidence research

**Status / horizon:** Planned. The next-checkpoint measurement: Now. The GPU kernels: Later.

**Observed limitation:** one checkpoint has been measured, `StrandsAgents/strands-decider-2B-hobson-v21`; the change from
CPU float32 to GPU bfloat16 was validated per question and per task before adoption
([Server variants](bench/RESULTS.md#server-variants-ruling-48-21-oct-2026)).

**User outcome:** a new checkpoint or a lighter configuration is adopted only when the evidence supports it; otherwise the
current one stays.

**Decider's decision:** every one it makes; R1 measures them.

**What application code / Bedrock / the owner controls:** date arithmetic, hard policy and authorization stay in code
whatever the checkpoint. Bedrock is unchanged. The adoption decision is Dan's, on the declared criteria.

**Work packages:**

1. The next checkpoint,
   [`StrandsAgents/strands-decider-2B-qwen3.5-v1-2610`](https://huggingface.co/StrandsAgents/strands-decider-2B-qwen3.5-v1-2610),
   measured against the same questions and floors on the frozen fresh set, with adoption criteria declared before the
   run, as the GPU adoption was done. The current checkpoint stays if nothing meets them. A checkpoint that needs
   different prompts or floors is a separate developed configuration, validated separately (Now).
2. The GPU kernels for the laptop's card (`causal_conv1d` and `flash-linear-attention`, not installed in the measured
   runs): a laptop optimization off the Hub path. They may not build for the card's Blackwell architecture (Later).

The validation discipline below applies to every performance candidate, the CPU variants (canonical under M5,
[M5.1](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/20)) included.

**Dependencies:** none for 1. The CPU variants and the photo path's checkpoint change follow 1.

**Completion criteria (proposed):** a report with paired per-question decisions, confidence-floor crossings, confident
errors and the resulting courier-action changes, against the current checkpoint, on the same set and the same
questions and floors; the adoption criteria declared before the run; the result published whichever way it falls.

**Evidence to publish:** the threshold declaration, the report with its replayable export, and the per-task change list,
as in Server variants.

**Risks or unresolved choices:** the checkpoint's model card lists configuration keys that released strands-decider
versions do not read, each at its off value; that the pinned release loads it unchanged is confirmed in the run.

**Tracking:** [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/6) · [parent issue](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/24) · sub-issues
[R1.1](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/25), [R1.2](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/26)

### Housekeeping

The notebook's rendered export (`notebooks/gatekeeper_triage.html`) is that of the live run of 21 October 2026. A
refresh from a new live run is planned at Now; it is not done ([issue](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/27)).

## Evaluation policy

### Standing rules

- Labels are committed, and hashed, before any model call.
- The configuration is frozen before the run.
- Every report is regenerated from a committed export with no model, no network and no key.
- A target is declared before the run that tests it.
- Negative and mixed results are published.
- Development fixtures are kept apart from fresh sets.

### Measurement

Each performance candidate is a versioned inference configuration: checkpoint, weights and quantization, precision,
runtime, kernels, preprocessing, prompts, floors and policy. Against the same declared workload it reports four
measurement families:

1. **Memory:** footprint and host headroom: the Windows committed figure kept for continuity, plus the peak resident
   memory of the process tree, host RAM, swap behavior, any out-of-memory event, and device memory where it applies.
2. **Responsiveness:** per-operation p50 and p95 latency, with the gate window and the six-question read kept apart; cold
   start and warm cache recorded separately; timeouts and backlog under the intended arrival pattern.
3. **Power and energy:** idle and load power and the energy per completed workload where measurable, with the instrument
   and its boundary stated. Incremental energy above idle is labeled separately; CPU-package telemetry is never
   compared with whole-system wall measurements as if they were equal; "unmeasured" is written where power cannot be
   measured.
4. **Decision behavior and quality:** paired per-question decisions, confidence-floor crossings, confident errors and
   the resulting courier-action changes, including project precision and coverage and the source gate's false clears
   and false holds.

Aggregate accuracy delta is a summary, not the adoption gate. A changed checkpoint, precision, quantization, runtime or
kernel is validated under R1 before adoption even if aggregate accuracy is unchanged, with the checkpoint's identity
preserved and the numerical change described as what it is. Targets and permitted tradeoffs are declared before the
acceptance run; a target chosen after inspecting feasibility probes is not predeclared for those probes. Each result
states whether it covers the text Hub only or the laptop-plus-Hub photo workflow.

### The frozen fresh set

The fresh set ([labels](bench/fixtures/fresh/labels.json)) keeps its name and records and serves as the regression and
comparison set. Selecting a winner on it does not make it a fresh held-out result. A new generalization claim after
selection or tuning needs a new held-out set, its labels committed and hashed before its first call and the candidate
frozen before the run. The original set is never relabeled.

### Stage gates

A threshold declaration is published before each acceptance run. It names the candidate's versioned configuration and
the approved baseline; the workload and its arrival pattern (idle, burst, sustained); the memory budget; the latency
objectives per operation; the power instrument and its boundary, or "unmeasured"; the functional, regression and
safety cases; the permitted tradeoffs; any tolerance or non-inferiority claim with the sample size that supports it;
and whether it covers the text Hub only or the laptop-plus-Hub photo workflow.

- **Stage one passes** when a reproducible candidate is supported by the architecture and runtime, fits the declared
  memory budget, passes the declared functional and behavior checks, and meets a predeclared resource and latency
  objective against the approved baseline, with tradeoffs and failures published. If none qualifies, the baseline
  stays and the stage is recorded as not passed.
- **Stage two passes** when the complete text courier and server run on the target host within the declared memory,
  responsiveness and power constraints through the stated idle, burst and sustained workload, restart and recovery,
  and the UPS shutdown test, with persistent state verified, no duplicate writes, and correct behavior when a service
  is unavailable. An isolated model read is not a pass.
- **Stage three passes** when the selected configuration passes the same checks on the actual board, the build, flash
  and boot procedure is reproduced clean, and the parts list, wiring, image provenance and recovery instructions match
  the tested system. Feasibility is published only when measured on that board.

Every stage includes a behavior review: no new prohibited release, unauthorized action or duplicate write in the
declared regression cases; no additional sensitive-source clearance in the declared safety cases against the accepted
baseline, stated as an observed test condition; per-question and coverage tradeoffs reported, and any permitted
regression declared and reviewed; any tolerance or non-inferiority claim predeclared and supported by the sample size,
and inconclusive evidence reported as inconclusive.

## The tracking board's views

The public Project has three views:

| view | layout | shows |
|---|---|---|
| Development overview | table, grouped by Phase | every item with its Phase, Horizon, Priority, Status, milestone and labels |
| Implementation | board, columns by Status | the work from Planned to Done |
| Timeline | roadmap | not created until real date estimates exist |

Fields: Phase (M1–M5, R1, Housekeeping), Horizon (Now, Next, Later, Exploratory), Priority (High, Medium, Low; until
items are ranked within a horizon, Now items are High, Next items Medium, Later and Exploratory items Low), and the
Project's own Status (Planned, In progress, Done), starting at Planned for every item. No date field exists until real
date estimates do.

## Tracking

The public board: [Ratatoskr Development Roadmap](https://github.com/users/dan-lee-odinson/projects/3), with every item below at status Planned.

| workstream | milestone | parent issue | sub-issues |
|---|---|---|---|
| M1 — Project context and owner-assisted completion | [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/1) | [#1](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/1) | [#2](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/2) (M1.1), [#3](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/3) (M1.2), [#4](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/4) (M1.3) |
| M2 — Photographed commitments that survive real input | [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/2) | [#5](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/5) | [#6](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/6) (M2.1), [#7](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/7) (M2.2), [#8](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/8) (M2.3) |
| M3 — Secure owner review with real local content | [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/3) | [#9](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/9) | [#10](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/10) (M3.1), [#11](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/11) (M3.2), [#12](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/12) (M3.3), [#13](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/13) (M3.4) |
| M4 — The companion app and wearable input | [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/4) | [#14](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/14) | [#15](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/15) (M4.1), [#16](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/16) (M4.2), [#17](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/17) (M4.3), [#18](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/18) (M4.4) |
| M5 — The Hub | [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/5) | [#19](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/19) | [#20](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/20) (M5.1), [#21](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/21) (M5.2), [#22](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/22) (M5.3), [#23](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/23) (M5.4) |
| R1 — Checkpoint and confidence research | [milestone](https://github.com/dan-lee-odinson/ratatoskr-demo/milestone/6) | [#24](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/24) | [#25](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/25) (R1.1), [#26](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/26) (R1.2) |

Housekeeping: [#27](https://github.com/dan-lee-odinson/ratatoskr-demo/issues/27), the notebook export's refresh.

## Change history

- 23 October 2026: first version: the direction, the baseline and its limitations, six workstreams, the evaluation
  policy, and the tracking board.
