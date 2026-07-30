# Changelog

## v0.3

### Theme customization
- "API Configuration" renamed to "Configuration", now a two-page window
  (API Keys / Theme) behind a page selector at the top.
- New Theme page: pick a background color and an accent color — presets
  as circular swatches (Pitch Black / Natural Black / Grayish Black for
  background; Green/Red/Purple/Pink/Violet/Orange/Blue/Teal for accent)
  plus manual hex input for either. Text color is intentionally not
  customizable, per request.
- One background color generates the full BG1-BG4/glass/border shade
  ladder via a fixed lightness step; one accent color generates its own
  hover/dim variants — nobody has to pick five shades by hand.
- Applying shows a confirmation (warns unsaved files will be lost, since
  it restarts the app) and then closes and relaunches Meta Zone
  automatically. Verified the actual relaunch mechanism directly: spawned
  a real subprocess the same way Apply does, confirmed it launches and
  stays running, and separately confirmed a genuinely fresh process
  picks up a saved custom theme correctly. Works both from source and as
  the frozen EXE (uses sys.executable when frozen, since a packaged
  build has no Python interpreter to hand a script to).

### Compact View + Grid + Pagination
- New View Settings controls in the results header: Expanded/Compact
  toggle, 1-4 column buttons, and page navigation.
- New CompactEditCard: small thumbnail, genuinely editable title/
  description/keywords boxes with live character/keyword counters,
  icon-only regenerate button — no bordered info box, no full status
  chrome.
- Architecture note: rather than rewrite the virtualized infinite-scroll
  system (already hardened through several rounds of real bug fixes —
  overlap, the buried collapse button, the edit-loss bug), Compact View
  and >1 columns use a separate, simpler paginated grid renderer instead.
  Expanded + 1 column (the default) keeps using the original,
  unmodified virtualized scroll — confirmed via regression test that it
  still works exactly as before.
- Tested end-to-end: 75-file batch, Compact + 3 columns, confirmed exact
  page counts (50 + 25), edited a card, navigated away and back,
  confirmed the edit survived — same edit-loss protection now covers
  page navigation, not just scrolling. Also confirmed live generation
  results stream correctly into compact-grid cards in place.
- View settings persist across restarts.

### Modern dropdown styling
- CTk's dropdown is a raw tkinter.Menu under the hood — on Windows its
  border is native OS chrome that can't be recolored through any CTk
  setting (a real Tk limitation, not a missed option). Built a proper
  replacement (ModernDropdown) using a borderless custom popup instead,
  applied to Platform and File Type.

### Collapsible control panel
- Thin collapse tab on the sidebar's right edge; matching expand tab
  appears on the card area's left edge once collapsed, freeing the full
  window width for cards during generation.

### Keyword ordering
- Strengthened the prompt to explain why keyword order matters (Adobe
  Stock weights early keywords more heavily in search) and what to
  prioritize first (main subject/action) vs. later (mood/color/style).
  Confirmed nothing downstream (single-word enforcement, copyright
  filtering, dedup) ever re-sorts the list — it's a pure order-
  preserving filter chain.

### Bug fixes
- Description/keyword bleed: when Description is off, some files were
  getting a second batch of keywords parsed into the description field
  (keywords generated twice under two different labels). Fixed by
  force-emptying description whenever the toggle is off, regardless of
  what the model returns.
- CSV auto-save/Save now use numbered suffixes (#folder.csv, #folder
  (1).csv, #folder (2).csv, ...) for separate batches from the same
  folder, instead of silently overwriting a previous export. Verified
  against the exact 3-batch scenario described.
- Vector titles double-stating "vector illustration" (once naturally,
  once as a trailing summary clause) — strengthened the prompt and
  added a code-side safety net that strips the redundant restatement,
  freeing character budget for real content.
- Punctuation restrictions: title/description now strip everything
  except comma, period, hyphen; keywords strip all punctuation
  including hyphens.
- Platform title/description limits now actually lock the slider
  ceiling — switching to a stricter platform clamps down, but a
  manually-lowered value survives switching back. Corrected Adobe
  Stock's cap to 200 (was stored as 150).
- Embed window's "Processing" button text going invisible — same fix
  pattern as the Generate button.
- Embed folder browser now starts from the current folder instead of
  Documents; added drag-and-drop for the File Location field.
- Replace Filename toggle (hides Remove Copyright, which stays fully
  functional) — renames embedded files to the first 8 words of their
  title. Stress-tested with 30 files sharing an identical title:
  confirmed unique numbered filenames, zero data loss.
- Embedding is now parallel (up to 6 at once) instead of one file at a
  time. Caught and fixed a real race condition this exposed: two files
  with the same title being renamed simultaneously by different worker
  threads could have silently overwritten one file with another — fixed
  with a lock, then stress-tested under real concurrent load to confirm
  it holds.
- Removed "Content Theme / Videos" from Advanced Options entirely, and
  fixed a pre-existing bug this surfaced: Prompt mode was always
  sending every style name regardless of any toggle state.
- File Type dropdown color changed from cyan to green.

## v0.2

### Manual edits getting lost after scrolling (data loss — found root cause)
- Confirmed the real cause: the virtualized list destroys card widgets
  once they scroll out of view, but nothing ever read their edited
  text boxes back into the app's data first — so any hand-edited
  title/description/keywords were silently gone the moment that card
  was torn down, even before Save or Export was ever touched.
- Fixed at the root: every place a card gets destroyed (scrolled out
  of view, or collapsed/expanded) now reads its current text boxes
  back into the saved data first. Verified directly: edited a title,
  scrolled it far out of view (destroying the widget), and the edit
  was still there afterward.
- Also caught and fixed a second bug this surfaced: the first version
  of this fix merged the card's *entire* snapshot back (not just the
  edited text), which included a stale "status" field frozen at the
  moment the card was built — silently reverting a live status (e.g.
  "done" back to "waiting") on sync. Fixed to only sync the actual
  editable text fields.
- New **Save** button (top-right of the Generate tab, where Embed
  used to sit before it moved to the header): syncs every
  currently-open card's edits, then writes them straight to the
  working CSV on disk — no need to go through Export CSV's save
  dialog first, and it updates the same file whether or not you've
  manually exported yet.

### Floating scroll buttons
- Two arrow buttons, bottom-right of the card list, scroll exactly 3
  rows (a full screen, since 3 cards fit the viewport) per click —
  using the real per-card heights, so it still moves by exactly 3
  cards even with some individually expanded.

### Card overlap / missing collapse button (large batches)
- Root cause: the virtualized list used ONE fixed row-height estimate
  for the whole batch, so an individually-expanded card inside an
  otherwise-compact batch didn't get a taller row slot reserved for
  it — it just overflowed into the next row. Replaced with a real
  per-row cumulative-height table: each row is exactly as tall as its
  own card actually is, and every row after an expand/collapse shifts
  accordingly. Verified with an 80-file batch and two cards expanded
  simultaneously — no overlap.
- The collapse button was real but was getting visually covered by the
  description box's copy/paste buttons in the same corner (a z-order
  issue — it was built before those buttons instead of after). Moved
  it to build last and explicitly raised above everything else, and
  made it a labeled "⌃ Collapse" button instead of a tiny glyph so
  it's easier to spot.

### Scroll speed
- Mouse wheel scrolling felt sluggish because each wheel notch only
  moved ~20px against 130–274px tall rows. Increased the canvas's
  scroll increment (not touching CustomTkinter's shared event
  bindings) so a notch now covers roughly a full row.

### Sentence-completion fix (title/description)
- Prompt now explicitly tells the model to always finish as a complete
  sentence within the requested length — wrap up early rather than run
  out unfinished.
- Added `smart_trim()`: if a title still exceeds the limit, it's cut at
  the last complete sentence boundary instead of an arbitrary word
  boundary, so it never reads as cut off mid-thought.

### File type / content-type directives
- New "File Type" dropdown, right under Platform, styled like it —
  no longer buried in Advanced Options. Six options: Auto Detect,
  Vector, Illustration, Transparent PNG, White Background, Silhouette.
- Each option is now a MANDATORY, top-priority prompt directive (not a
  loose style hint) — Vector titles state the image is a vector
  illustration, Transparent PNG mandates mentioning the transparent
  background, White Background adds "on a solid white background",
  Silhouette states it's presented as a silhouette.
- `smart_trim()` now takes a `must_include` phrase: if trimming would
  cut off the mandatory content-type phrase, it shrinks the rest of the
  sentence further to make room instead, and appends the phrase if the
  model left it out entirely but there's still room.
- Moved Single Word Keywords into Advanced Options.

### Platform / title length
- Named platforms (Shutterstock, Getty, Adobe Stock, etc.) keep their
  real recommended title caps unchanged.
- The "General" platform preset's title cap raised from 150 to 300 —
  use this when you want the longer title and don't need to match a
  specific site's limit.

### Header
- Added a "Metadata AI" / "Embed" button pair to the title bar.
  "Metadata AI" is an inert, button-styled label showing the current
  mode (dark/gray, white text); "Embed" is a real button (green,
  black text, matching Generate/API Configuration) that opens the
  Embed window. Removed the now-redundant old Embed button from the
  Generate tab's toolbar.

### Embed window
- Fixed the dead space below "Start Embedding" — the window was
  hardcoded to 640px tall but the form only needs ~546px. Now opens at
  570px with a matching minsize.

### Architecture
- Added `workers/task_manager.py`: a bounded `ThreadPoolExecutor`-based
  worker pool, replacing the manual `threading.Thread` + `Semaphore`
  pair used for AI generation batches. Pause/stop/retry/progress
  semantics are unchanged — this is a mechanical swap of the
  concurrency primitive, not a behavior change.

## v0.1
Version reset to v0.1 as the new baseline. From here, each major update
bumps the version (v0.2, v0.3, ...) — edit `APP_VERSION` in
`core/constants.py`.

### Architecture
- Split the single `metadata_tool.py` file into a modular package:
  - `core/` — constants, prefs (load/save), stateless helpers (exiftool
    discovery, file matching, thumbnails, filesize formatting)
  - `engine/` — AI provider calls + failover, prompt building, response
    parsing
  - `ui/` — theme, drag-and-drop bootstrap, and each window/widget
    (main window, API key manager, embed window, result card, import
    progress dialog)
  - `app.py` — entry point
- Consolidated the color palette into a single `ui/theme.py`. The old
  file defined two full palettes — an unused legacy one and the real
  black-glass one that silently overrode it later in the file. Verified
  by usage scan before removing anything, and kept the two colors
  (`AMB`/`AMB2`) that turned out to still be in use.
- Fixed a real bug the split would otherwise have introduced: both
  `prefs_path()` and `find_exiftool()` resolved their base folder from
  `__file__`, which pointed at the *module's own* folder. After moving
  those functions into `core/`, that would have silently relocated
  `prefs.json` and broken exiftool discovery. Both now resolve relative
  to the app's entry point instead, matching the original behavior
  exactly.

### UI fixes (AI Generate tab result cards)
- Left info panel (thumbnail, filename, size, model, status, Regenerate)
  is now a fixed-width bordered box — it no longer expands or shrinks
  when Description is toggled on/off.
- Title box shrunk vertically; Keywords box given the freed space.
- When Description is on, Title/Description now split roughly 40/60
  instead of being nearly equal.
- Font sizes inside cards bumped up by one point.
- The drag-and-drop bar now hides once files are loaded (freeing space
  for cards) and reappears when the list is cleared. Drag-and-drop
  itself keeps working while it's hidden — the whole window is already
  a registered drop target.
- Net effect: real card height measured ~270px -> ~254px. Note: an
  earlier pass also doubled the thumbnail size, but that directly
  fought against shrinking the card, so it was reverted back to the
  original 60px per direction given during the session.

### Bug fix
- Added a one-shot automatic retry when a provider badly undercounts
  keywords (e.g. ~30 when 49 were requested): the request is retried
  once with a stronger correction prompt, keeping whichever attempt
  produced more keywords. This can't guarantee an exact count from
  every model, but should meaningfully reduce the undercount cases.

### Not done yet (flagged, not attempted this pass)
The full "Meta Zone v1.0 Performance Edition" architecture spec calls
for a ThreadPoolExecutor-based task manager, a multi-stage processing
pipeline, a live performance dashboard, structured logging, and a
persistent on-disk thumbnail cache. These would replace the app's
current (working) threading model, and doing that correctly needs its
own dedicated, carefully-tested pass rather than being bundled into a
UI-fix + file-split session. Tracked as follow-up work.
