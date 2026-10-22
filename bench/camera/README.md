# Camera photographs (ruling 58)

The camera evaluation's set: phone photographs of fictional materials made by hand, run once through the courier's
own photo path by `python bench/run_images.py --camera --run`. The protocol:

1. **The set.** Twelve photographs, `c01.jpg` to `c12.jpg`, taken by the owner on a phone, of materials he makes by
   hand, fictional throughout (invented names, invented numbers, nothing real): four that must not leave the
   machine, four reference, and four commitments; some angled, most handwritten, in ordinary room light.
2. **Labels first.** `labels.json` is written from the owner's description of each photograph (what it shows, its
   class, the key words on it, and for a commitment the expected task and project, or unsorted) and committed, with
   its sha256 in four groups of sixteen in the commit message, before any model is shown the set.
   `labels_template.json` has one entry per expected photograph. The labels are not edited after the run: a label
   found wrong afterwards is reported as a disagreement.
3. **The copies.** The repository's copies keep each photograph's picture data byte for byte, without its location
   metadata or any embedded motion clip; the originals stay with the owner.
4. **The input.** Each photograph is read as the courier reads any phone photograph: upright (its EXIF orientation
   honoured), with its long side at most 2000 px (RapidOCR's own limit; the vision server fits what it is sent to
   448 px itself), from a copy in memory; the file is not touched. A file over 12 MB is held as too large, and a HEIC
   file as an unsupported format, before any model.
5. **The run.** Both CPU servers up (the text server on 127.0.0.1:8000, the vision server on 127.0.0.1:8001), with
   the same frozen gates, floors, question and checkpoint as `bench/IMAGE_EVAL_2026-10-22_final.md`, and nothing
   else running on the laptop. Each photograph is placed, one at a time, in the watched folder of a courier whose
   state lives under `bench/raw/`, and goes through the courier's own cycle until it settles. For a photograph the
   labels call ordinary, the owner releases a gate's unsure hold with the Release button on its notice and answers
   an unsure scene with Commitment or Reference, each recorded as the owner's. A photograph the labels call
   sensitive is offered nothing: a sensitive hold has no button, and its lines stay on the local record. Todoist
   writes and every other Telegram line go to local records; only the Release and scene notices of ordinary
   photographs reach the owner's chat.
6. **The report.** `bench/image_eval_<date>_camera.json` and `bench/IMAGE_EVAL_<date>_camera.md`, the date being the
   UTC date the run started (ruling 55). Beyond the final run's tables, the report gives, per photograph, the OCR's
   recovered key words of those expected (an OCR miss counted apart from a model disagreement), the pixel size and
   orientation handling, and whether the label calls it angled or handwritten; and counts of automatic completions,
   owner interventions, confident disagreements with the labels, OCR failures, and cloud calls by class.
   `python bench/run_images.py --check` draws it again from its export alone.
