# Changelog

## v0.7 — Complete UI/UX & Card System Refactor, plus the recurring freeze (again) and a scroll bug batch

**The freeze, found again:** a **third** live instance of the exact bug
class already root-caused and fixed twice before (v0.6, v0.6.3) —
`self.after()` called directly from a background thread. This one was in
`embed_window.py`'s embedding pipeline, and it's the hottest instance yet:
up to 6 concurrent worker threads (the embed batch's own thread pool) all
doing it per-row, not once per batch from a single thread like the earlier
two. Also found it in `api_dialog.py`'s key-validation thread, and a
fourth, currently-dormant instance in a `widgets.py` thumbnail-loading
fallback path. Fixed all three the same way as before — every UI touch
from a worker thread now goes through a plain thread-safe queue, drained
only by a main-thread-scheduled poll; no Tk call originates off the main
thread anywhere in any of these three files now. Verified the queue drains
correctly under real concurrent load. Could not reproduce the exact
reported repro (Windows, clicking away to File Explorer and back mid-batch,
a 69-file 5500×3000 batch) in this environment — flagging this honestly
rather than claiming the repro itself is solved, but this is a confirmed
real bug matching the project's own established root-cause pattern, not a
guess.

**Card system & workflow — the requested refactor:**
- **Pagination removed entirely.** No pages, no Previous/Next, no page
  numbers. One continuously scrolling grid.
- **Manual column selector removed.** Both view modes now auto-fit column
  count to window width instead: Expanded 2 cols (small window) / 3
  (large), Compact 3 / 4 — two fixed tiers per mode, not a continuous
  card-width division, matching the spec exactly.
- **New card-creation workflow.** A path never gets a card while it's
  "waiting" or "working" — no empty cards, no placeholder cards, ever.
  A card is created exactly once, the instant its own metadata generation
  finishes (done or failed), already fully populated (thumbnail, filename,
  title, keywords, description, status), with a lightweight fade-in
  (~180ms color interpolation from background to the card's real color —
  CTk has no true alpha channel to animate against). Verified this holds
  even with Working View off, which needed a real fix: the debounced
  render that used to only fire when Working View was on now also fires
  whenever a newly-finished path doesn't have a card yet; and `_gen_done`
  now forces one final render to close a race where the very last
  completions in a fast/high-concurrency batch could still have a pending
  debounce timer when the batch itself ended.
- **Processing Queue stats** — Completed / Remaining / Avg time per image /
  Est. time remaining / current AI model / retry count, added to the
  progress bar area while a batch is running. Deliberate scope call: built
  into the existing progress bar strip rather than as a separate panel
  competing with the results grid for space — flagged as a design decision,
  not hidden as if it were the only option.
- **Compact card layout restructured** to a single vertical stack —
  Thumbnail → Status Badge → Filename → Metadata — replacing the previous
  two-column (thumbnail+status on the right, metadata beside it) layout.

**Sidebar:**
- The expand/collapse toggle is gone. The sidebar is now permanently the
  icon-over-short-label style that used to be the "collapsed" state —
  there is no other mode any more.
- Icons are noticeably bigger. (A single `CTkButton` can't mix two font
  sizes in one string, so each nav item is now a small compound widget —
  an icon label stacked over a text label, each with its own font — with
  manual hover/click/active-highlight handling replacing the button.)
- Labels unchanged text, +1pt font size, spacing rebalanced around the
  bigger icons.

**Dashboard:**
- **Blinking, root-caused:** `CTkLabel.configure()` forces a full internal
  redraw every call regardless of whether the value actually changed —
  and the dashboard was calling it on 20+ widgets every 4 seconds
  unconditionally. That's what read as the whole screen blinking.
  Added change-detection (only calls `.configure()` when a value genuinely
  differs) plus a brief color-pulse highlight on real changes, as a
  proxy for "fade in/out" since `CTkLabel` has no real alpha. Verified
  with an instrumented test: zero redundant `configure()` calls across
  repeated refreshes of unchanged data.
- **Layout reordered** per request: Recent Activity / Productivity
  Insights / System Status are now one row of 3 equal-width columns (the
  latter two existed already but were built and never actually shown,
  hidden behind the Quick Actions grid); the Activity (Last 7 Days) chart
  moved to its own full-width row below, height dropped 180px → 90px
  since it no longer needs to match Recent Activity's height.
- Same blink fix applied to the Recent Activity list and the 7-day chart:
  both now skip their (expensive, visibly flashy) full rebuild/redraw
  entirely when the underlying data hasn't actually changed since the
  last tick.

**Embedder drag-and-drop dead zone:** only the two narrow CSV/File-Location
rows were ever registered as drop targets. The popup embedder is small
enough that those two rows cover most of the window, so it's hard to miss
them; the full-page embedder sits inside the much bigger main window with
a lot of open space around those same two rows, and dropping anywhere else
landed on nothing. Registered the whole page as a catch-all drop target
too — same fix class as the Smart Workflow drag-and-drop bug from an
earlier session. Verified under a real tkdnd load: dropping a folder or a
`.csv` anywhere on the page now routes correctly.

**Scroll bug batch (found from live feedback on this same build):**
- **Scroll buttons barely moving:** root cause was relying on Tk's
  `yscrollincrement` for click distance, which turned out to be
  platform/build-dependent — a low or zero default on at least one real
  build made each click move almost nothing. Rewritten to compute pixel
  distance directly from an actual rendered card's height (2 card-heights
  per click) every time, independent of any Tk/platform default.
- **Scrollbar thumb not showing/draggable:** the floating ▲/▼ buttons'
  placement (`x=-14`) directly overlapped the built-in scrollbar's track —
  confirmed by measuring real widget geometry, not guessed. Moved to
  `x=-38` to clear it with a gap.
- **No locked position at either end** ("top cards keep moving down, then
  there's nothing above them, no lock"): the scroll position wasn't being
  re-clamped against the *current* content size on every click, so it
  could drift past the actual first/last card while cards were still
  resizing/relaying-out live during generation — Tk doesn't automatically
  re-clamp an existing scroll position when the scrollable content's
  extent changes after the fact. Every click now recomputes the real
  content bbox and clamps to it fresh, so each click is self-correcting
  regardless of what happened to the layout since the last one. Also set
  `yscrollincrement=1` (was left at whatever the Tk build defaulted to) so
  `yview_moveto` can't quietly snap to a coarser increment than intended.
  Verified exact locks at both ends even after 30-40 rapid clicks well
  past either boundary.

Delivered as changed-files-only: `ui/main_window.py`, `ui/widgets.py`,
`ui/dashboard.py`, `ui/embed_window.py`, `ui/api_dialog.py`,
`core/constants.py`.

## v0.6.3 — Recurring freeze root-caused, Working View readability, drag-scroll, and a bug batch

**Item 5, the recurring "Not Responding" freeze:** found a second, much more
frequent instance of the exact bug class already fixed once for the
online-status loop — the generation-completion callback was calling
`self.after()` directly from a background thread, on *every single batch
you run*. Rewrote the entire generation status pipeline (working/done/
failed updates, the completion signal, the ExifTool check, the
online-status loop) through a proper thread-safe queue that only the main
thread ever drains — no Tk call now originates off the main thread
anywhere in that path. Verified end-to-end with a real generation run.

**Other real bugs found and fixed:**
- The floating ▼/▲ buttons next to the results grid were actually
  changing pages, not scrolling — restored to their real function (mouse-
  wheel-equivalent scroll of the current page); the separate ◀/▶ Page Nav
  buttons still handle pagination.
- Moving the window (not resizing it) was still triggering the deform/
  reform flash — tightened the resize handler to check the event's actual
  width before doing anything, since a pure drag fires `<Configure>` on
  child widgets in some window managers even though nothing about their
  size changed.
- Found a real mismatch from the earlier thumbnail-size unification: the
  card *frame* was resized to 80px but the actual *requested* thumbnail
  resolution was still defaulting to 58×58 — fixed in both the live-
  display path and the page-navigation rebind path.

**New this round:**
- Whole-batch thumbnail prefetching — importing now warms the disk cache
  for every image immediately in the background, not just the page
  currently on screen, so paging through a large batch later hits the
  cache instantly. Verified: 8 images → 16 cache files (both view-mode
  sizes) within 1.5s of import.
- Clear All now also wipes the thumbnail cache (background thread, since
  the folder can hold many files) — never touches prefs.json, which lives
  in a different folder entirely.
- Click-and-drag scrolling on the results grid — hold and move the mouse
  to scroll live, like a touch gesture. Bound directly on each card and
  its thumbnail (not just empty canvas background, which cards leave
  almost none of).
- Working View now holds a just-finished card visible for a few seconds
  instead of swapping it out the instant it completes — the "only a
  blink before it's gone" complaint. The page label now shows both counts
  (e.g. "⟳ Working (10)  ✓ (3)") instead of lumping everything under
  "Working".
- Nav now shows short labels (Home/Metadata/Smart/Embed/Prompt/P2P/API/
  Setting/License/Help) under each icon even in collapsed mode; expanded
  mode is unchanged.
- Investigated the "concurrency=20 feels slower than older versions"
  report: audited the whole concurrency path (a standard bounded
  `ThreadPoolExecutor`, unchanged this session) and found no code-level
  bottleneck. Most likely explanations are free-tier AI provider rate
  limiting at higher concurrency, or a perceptual effect from more cards
  being visible at once — flagging this honestly rather than claiming a
  fix for something not found. The app already tracks real "Avg.
  Processing Speed" (img/min) from actual completion data in
  `core/stats_db.py`, currently just not visible on screen since that
  panel was hidden in v0.6.1's dashboard restructure.

## v0.6.2 — Thumbnail disk cache + lazy page init (rest of the performance directive)

- **Thumbnail disk cache**: resized thumbnails are now saved once to a
  shared cache folder (next to prefs.json in `C:\MetaZone\.cache\thumbs`)
  and reused on later imports of the same file — mtime-keyed, so editing
  or replacing a source image automatically invalidates its cached
  thumbnail without needing to hash the whole file. Includes light
  automatic cleanup so the cache can't grow unbounded. Verified
  end-to-end: cache hit path measured at ~0.6ms vs ~200ms for a cold
  generate on a test image, and confirmed real cache files get created
  when importing into the actual running app.
- **Lazy page initialization**: Meta Embedder, API Manager, and Settings
  now build the first time you actually navigate to them instead of at
  startup — profiling showed this was costing ~1s of App() construction
  for pages a given session might never visit. Startup measured at
  3.6s → 2.5s. Once built, each page is cached exactly as before (never
  rebuilt, never destroyed) — this only changes *when* construction
  happens. Verified every page, including the newly-lazy ones, still
  builds correctly on first visit with zero errors.
- Dashboard, Metadata/Prompt Generator, and Smart Workflow stay eager —
  Dashboard is the landing page every session hits immediately, and the
  other two are commonly the very next thing opened, so deferring them
  had little real-world upside for the added risk.

## v0.6.1 — Performance root cause found & fixed, Working View, and a bug batch

**The big one:** measured `_render_page()` at **6.5 seconds** per grid-column
change or page navigation at a 372-image scale — completely freezing the
main thread. That's the real explanation behind several reported symptoms
that looked unrelated: the scrollbar and mouse wheel appearing "stuck"
(the whole app was frozen, not broken), the visible deform/reform flash
on grid changes, and a 1-column Expanded layout looking stuck until
restart. Root cause: every trigger (page nav, grid-column change, any
re-render) destroyed and rebuilt every card widget from scratch — for a
CTkTextbox-heavy Expanded card, construction was always the expensive
part, never the data. Rewrote the renderer to reuse a pool of already-
built card widgets via a new `rebind()` method (added to both
`MetaResultCard` and `CompactEditCard`) instead of destroy+reconstruct.
Re-measured after the fix: page navigation **6.5s → ~0.18s** (35x
faster), grid-column changes **6.5s → ~0.5s** (13x faster) — profiled the
remainder and confirmed it's legitimate CustomTkinter canvas redraw, not
waste.

**Working View** — new toggle next to the grid-column selector. While
generation is running, the results grid shows only the cards currently
being processed (exactly as many as your Concurrent Generation setting),
advancing live as each one finishes — in both Expanded and Compact.
Automatically reverts to normal pagination once nothing is actively
processing. Verified end-to-end with a scripted run.

**Other fixes this round:**
- Added an Embed button to Metadata Generator, to the left of Clear All —
  shown only after a full, natural generation completion; stays hidden
  through Stop/Pause and disappears again on Clear All. Verified with
  three separate scripted scenarios (natural completion, Clear All,
  Stop).
- Found and fixed a second real crash bug while working on the above:
  `CompactEditCard.get_result()` was defined twice — the second
  (winning) definition crashed on `None._boxes`, which would have broken
  Save/Export CSV any time it ran from Compact view.
- Concurrent Generation max raised from 10x to 20x.
- Thumbnail size unified to 80px long edge in both Expanded and Compact
  (a stale code comment had claimed Expanded was already 120px — it was
  actually still 60px).
- Title/Description/Keyword counters split into their own accent-colored
  label next to the field name, instead of one small gray combined label.
- Nav menu button alignment fixed — it was sitting in its own narrower
  frame instead of being sized/packed identically to the icon buttons
  below it.
- Dashboard font sizes increased across the board; Quick Action button
  labels bumped further so they're clearly more prominent.
- Left honestly unfinished: the theme-change tempdir-warning/false
  ExifTool-missing report needs real Windows testing to make further
  progress on — the Python-level hardening from v0.6 is still in place,
  but this can't be verified or root-caused further from this
  environment. Lazy page initialization (building non-Dashboard
  workspaces on first visit instead of at startup) from the performance
  directive is also not done — page-switching itself is now fast via the
  caching fix above, so this was deprioritized in favor of the
  higher-impact items in this list.

## v0.6 — Nav/Dashboard restructure, inline pages, Process All, and a large bug-fix batch

**Real bugs found and fixed** (all root-caused and verified via a real
Xvfb+tkinter test harness this round — screenshots, pixel-diffing, and
mocked end-to-end runs, not just static review):
- **App freeze after running a while** — the online-status background
  thread was calling `self.after()` directly, which isn't thread-safe in
  Tkinter and was silently corrupting the UI's event-loop state every 8
  seconds until it eventually hung. Fixed to reschedule on the main
  thread only.
- **Smart Workflow drag-and-drop ("0 files loaded")** — the Smart
  Workflow panel sits on top of and covers every drop target Standard
  Workflow registers, but was never itself registered as one, so a drop
  onto it silently did nothing. Registered it, and made the file-count
  label update live on every import path (browse, drag-drop, and the
  large-batch async import).
- **Prompt-to-Prompt: 50 requested → only 6 delivered, one a stray
  fragment** — batches ran concurrently, so most started with an empty
  "avoid duplicates" list at the same moment, produced near-identical
  variations of each other, and dedupe collapsed them down hard (worst
  at Low creativity, which explicitly asks the model to stay close to
  the original wording). Fixed with a bounded catch-up loop that tops up
  any shortfall using a real avoid-list, raised the token budget for
  these text-only batches (2200→4000 — a batch of 10 detailed prompts
  could get cut off mid-list), and added a filter that drops
  preamble/fragment lines. Verified by simulating the exact failure
  mode: 50 requested → 50 delivered.
- A crash bug introduced partway through this same round (an edit had
  accidentally nested the rest of `SmartWorkflowPanel._build()` inside
  a helper method) — caught immediately by actually running the app
  under a virtual display instead of only compiling it.

**Nav & page restructure**
- Left nav is now always a vertical icon strip; the menu button expands
  it to icon+label and back, it never becomes anything else.
- Reordered to: Dashboard, Meta Generator, Smart Workflow, Meta
  Embedder, Prompt Generator, Prompt to Prompt, API Manager, Settings,
  License, Help.
- Meta Embedder, API Manager, and Settings are now real inline nav
  pages — not popups. API Manager and Settings share one underlying
  content frame (`APIManagerContent`) with two modes so the popup
  shortcut and the nav page can never drift apart; same pattern for the
  Embedder (`EmbedContent`). The old popups still exist for quick access
  (Metadata Generator's sidebar shortcut opens API Manager as a popup)
  but are thin wrappers around the same shared content now.
- Removed the sidebar Standard/Smart Workflow toggle and the
  Metadata/Prompt mode toggle — the nav is the only place those are
  chosen now. Removed the permanent top "Metadata AI"/"Embed" buttons.
- Settings is now the only place Theme is reachable from; API Manager
  is API keys only, renamed from "Configuration".

**Dashboard**
- Productivity Insights and System Status are hidden for now, replaced
  in that exact spot by a 2×3 Quick Actions grid.
- "Idle"/"Empty" moved to the top-right corner of the Running
  Tasks/In Queue cards.
- Added dashboard-only Stop All (pauses every running workflow without
  closing anything) and Refresh (resets the view only, never touches a
  running process) next to the online indicator.
- Confirmed nothing in the stats/activity area is click-bound.

**Smart Workflow**
- Thickened the progress bar to match the rest of the app, added a live
  imported/processing/good/bad stats row underneath it.
- Added a "Process All" toggle next to Auto-Embed: on, metadata
  generation auto-starts on Good + Needs Review right after Quality
  Inspection with no manual step; off (default) keeps today's manual
  selection step. Verified end-to-end both ways with a mocked run.

**Other**
- Real app icon (dark badge, green "MZ") now shows in every window's
  titlebar and the Windows taskbar/EXE — wired into the PyInstaller
  build.
- API Manager: explicit "Apply to All Keys" action for switching a
  provider's model, with visible confirmation.
- Lightened the accent color presets that read as too dark against the
  dark background (Red/Purple/Pink/Violet/Blue) to match Teal's
  brightness; Green/Orange/Teal were already fine.
- Hardened the theme-change restart path and the ExifTool detection
  against the "second theme change in a row" failure seen in testing —
  best-effort, since the exact Windows/antivirus interaction can't be
  fully verified outside real Windows.
- prefs.json now lives in a shared `C:\MetaZone` folder instead of next
  to the EXE, with automatic one-time migration of existing settings,
  so it survives switching between installed versions.

**Not done yet, left honestly unfinished:** Prompt Generator is still
not a fully independent page (it shares Metadata Generator's upload/
results area, though the nav-only mode switch is in place); no general
resize/stutter pass beyond what was already debounced; the "12 cards /
constantly deforming" report could not be reproduced (card positions
were pixel-identical before/after a content update in testing) —
needs more detail to keep chasing productively.

## v0.5.1 — Prompt-to-Prompt Generator (Part 2 of the v0.5 spec) + provider updates

**Prompt-to-Prompt Generator** — a brand-new workspace, fully wired into
the nav (no longer a "coming soon" placeholder):
- One prompt in, N new variations out (5/10/20/50/100), with Creativity
  (Low/Medium/High) and Prompt Style (Maintain Original/Commercial/
  Creative/Minimal/Highly Detailed) controls.
- Runs as background batches (10 prompts per AI call) through the app's
  existing bounded worker pool — Progress/Pause/Cancel all work, and
  navigating to any other workspace never interrupts a run in progress.
- Output list: per-prompt Copy, Select All, Copy All, Export TXT/CSV,
  and Regenerate Selected (replaces only the checked prompts, keeps the
  rest). Verified with a scripted test including that regenerating
  selected prompts doesn't clobber the normal Generate flow afterward —
  an actual bug caught while testing (see below).
- Duplicate-safe: each batch is told what's already been generated so
  far to steer away from repeats, plus a final normalized-text dedupe
  pass across the whole result set.
- Completions are recorded to the same stats DB the Dashboard reads
  from, so its "Total Prompt-to-Prompt Generations" figure is real
  starting now, not perpetually zero.

**Engine change enabling the above**: every AI provider caller
(`engine/ai_providers.py`) previously *required* an image — there was
no way to send a text-only request through the app's AI engine. Rather
than write a second, duplicate set of "text-only" callers (which the
Smart Workflow spec's "never duplicate AI request logic" principle
argues against), each existing caller now accepts `path=None` and
simply omits the image part of the request. Zero behavior change for
every existing caller — Standard/Smart Workflow/Prompt Generator always
pass a real path, so their requests are byte-for-byte the same as
before; verified by inspecting the actual request payload built with
and without a path.

**Bug found and fixed while testing this**: `_regenerate_selected` was
reassigning the engine's `on_complete` callback to a one-off closure
and never restoring it — so after using "Regenerate Selected" once, the
next normal "Generate" click would silently misbehave (still running
the regenerate-merge logic against a stale list). Fixed by using a
stable single callback with a "pending keep" flag instead of swapping
the callback itself.

**Provider/model updates**:
- Added the current Gemini 3.x Flash lineup — 3.6 Flash, 3.5 Flash, 3.5
  Flash-Lite, 3.1 Flash-Lite, and 3 Flash (Preview) — verified against
  Google's live models documentation rather than guessed. Also dropped
  Gemini 2.0 Flash, which that same documentation confirms was shut
  down June 1, 2026 — leaving it selectable would have just meant
  picking a dead model.
- Claude is now hidden from the AI Providers page and skipped during
  generation failover — it has no free API tier, and this app is
  free-providers-only. Uses the same `HIDDEN_PROVIDERS` mechanism
  already in place for Grok/Groq, so nothing structural changed and
  Claude support can be un-hidden later with a one-line change if that
  ever matters.

## v0.5 — Dashboard & Global Navigation (Part 1 of the v0.5 spec)

The biggest structural change yet: Meta Zone is no longer one flat
window — there's now a permanent left navigation rail and every tool is
its own workspace. Switching workspaces never interrupts anything
running in the background; verified with a scripted test that starts a
generation batch, navigates through 4 different pages mid-run, and
confirms all files still complete and get recorded correctly.

**Dashboard** (now the landing page on launch):
- Today's Statistics, Lifetime Statistics, AI Usage, Productivity
  Insights, System Status, and Recent Activity — every number comes
  from a new persistent SQLite store (`core/stats_db.py`) fed by real
  completion events (metadata generation, embedding, prompt generation,
  Smart Workflow runs). Nothing is simulated — a fresh install shows
  zeros and "—", not sample data. Cost figures are explicitly labeled
  "Est." since there's no real per-provider billing API to pull from.
- A hand-drawn 7-day activity chart (plain Tkinter Canvas, no new
  charting dependency).
- Quick Actions that jump straight to the relevant workspace, including
  "Resume Last Project" for an interrupted Smart Workflow run.

**Global Navigation**: Dashboard, Smart Workflow, Metadata Generator,
Metadata Embedder, Prompt Generator, Prompt-to-Prompt Generator, AI
Providers, Settings, License, Help. Metadata Generator / Smart Workflow
/ Prompt Generator all share the exact same underlying workspace and
logic as before (no duplication) — the nav items just drive the
existing workflow/mode toggles instead of introducing a second
implementation.

**Incidental bug found and fixed while testing this**: re-navigating to
an already-active Metadata Generator/Prompt Generator/Smart Workflow
page was calling the same internal mode-switch logic every time, which
unconditionally cleared results — including a batch that was still
running. Confirmed via test: without the fix, navigating away and back
mid-generation silently dropped completed files back to "waiting" and
the run finished 3 files short. Fixed by making the mode/workflow
switches a no-op when already in the requested state.

**Known limitations in this delivery** (spec's remaining pieces, coming
next):
- Prompt-to-Prompt Generator is not built yet — its nav item says so
  honestly rather than faking a working page.
- Metadata Embedder, AI Providers, and Settings are real, working pages
  today, but each opens its existing dialog rather than being fully
  redrawn inline — full in-page embedding is the other piece of this
  spec still pending.
- Settings doesn't yet have a "Reset Lifetime Statistics" button wired
  up, even though `stats_db.reset_lifetime()` exists and works.
- Not yet stress-tested with a real multi-thousand-file history in the
  stats DB — only small synthetic batches so far.

## v0.4.1 — Smart Workflow keyword ordering

- Stage 5 (Metadata Optimization) never actually implemented the spec's
  "Keyword ordering"/"Keyword importance" checks — it only scored
  metadata, it didn't verify or fix ordering at all. Stage 4's prompt
  already asks the AI for most-relevant-first keywords (same instruction
  Standard Workflow uses), but that was a soft request with nothing
  enforcing it in code.
- Added: Stage 5 now re-ranks each result's keywords so any keyword that
  actually appears in the title (the clearest "this is the central
  subject" signal) moves toward the front — the most relevant keywords
  now genuinely show top to bottom. It's a stable sort, so the AI's own
  relative ordering is preserved within each relevance tier rather than
  discarded; nothing is fabricated and no extra API calls are made.
  Verified with a unit test confirming title-matching keywords move to
  the front while both groups keep their original relative order.

## v0.4 — Smart Workflow (Beta)

A brand-new, fully separate opt-in workflow — Standard Workflow is
untouched and stays the default. Toggle between them from the sidebar;
switching doesn't destroy or rebuild either mode's widgets (raised/
lowered over the same area instead), and files are imported the normal
way in either mode.

Seven automatic stages, each with its own progress indicator:
1. **Preview Generation** — every image gets a temporary ~1024px-long-
   side preview before anything else happens; originals are never
   touched until embedding, and previews are deleted at the end.
2. **AI Quality Inspection** — every preview is checked for blur, AI
   artifacts, deformed faces/hands, missing/duplicate body parts,
   logos/watermarks/signatures, visible text, and copyright-sensitive
   content, and classified 🟢 Good / 🟡 Needs Review / 🔴 Rejected with
   a confidence score. Nothing is permanently rejected at this stage —
   classification only.
3. **Image Selection** — shows the Good/Review/Rejected counts and lets
   you choose Good Only / Good + Needs Review / All Images before
   metadata generation runs.
4. **Metadata Generation** — reuses the exact same prompt-building,
   parsing, and AI-failover engine as Standard Workflow (no duplicated
   logic), sending only the preview, never the original file.
5. **Metadata Optimization** — scores each result (missing/short
   keywords, missing description, flagged trademark/copyright terms,
   excess punctuation) into a Metadata Quality percentage.
6. **Embedding** — one toggle: auto-embed into the originals (reusing
   the same embed helper Standard Workflow's Embed window uses) or
   skip straight to CSV-only.
7. **Organization & Cleanup** — sorts originals into Ready Upload /
   Needs Review / Rejected folders, writes the CSV and a log, deletes
   the temporary preview cache, and produces an exportable TXT
   processing report (totals, scores, timing, provider used, errors).

**Interruption recovery**: progress is checkpointed to disk after every
stage. If Meta Zone closes mid-run, the next launch detects it and asks
to resume from the last completed stage. Verified via a scripted stop-
mid-generation-then-resume test — and along the way, found and fixed
two real bugs in this before it ever shipped: the checkpoint was
recording the stage that had *just finished* instead of the next one to
run, which would have silently re-run (and re-billed) that stage on
every resume; and generated metadata/scores were never actually being
saved to the checkpoint at all, which would have silently lost all
generated results on any resume past Stage 4. Both fixed and confirmed
with a test asserting zero redundant AI calls and zero data loss across
a real interruption.

Performance: never loads the whole batch into RAM, uses the same
bounded worker-pool pattern as Standard Workflow's generation (not
unbounded threads), and is designed against 5,000+ image batches —
verified for correctness end-to-end on small batches with mocked AI
calls; full-scale throughput/memory behavior hasn't been exercised yet
on a real multi-thousand-file batch.

## v0.3.2 — progress bars

- Every progress bar in the app (Generate tab, Import dialog, Embed
  window) was a thin 6px line that was easy to miss. Thickened all
  three to a consistent 14px, fully rounded, with a subtle border for
  definition — same accent color, just far easier to actually see
  progress at a glance.

## v0.3.1 — bug-fix batch

### Root-caused: the card list overlap/garbling, "imports stop showing as cards"
- The results area actually had two separate rendering systems fighting
  each other: a hand-rolled virtualized "infinite scroll" (place()-based
  absolute positioning + a row-height table) for Expanded/1-column, and a
  separate paginated grid for Compact/multi-column.
- Reproduced with a headless Xvfb + tkinter test harness: importing in
  two batches that together crossed the old internal 60-file
  "auto-compact" threshold silently shrank already-built cards' reserved
  row height (274px → 134px) in the height table WITHOUT rebuilding the
  actual widgets — so a still-274px-tall card ended up overlapping the
  row below it by ~140px. This is the confirmed cause of "the first
  dozen or so files look fine, then everything after looks like one
  garbled/merged card."
- Fix: removed the whole dual-system architecture (virtualization,
  row-height table, the 120ms scroll-poll, place()-based positioning)
  and unified every view mode onto one simple paginated grid renderer.
  A page is always bounded by page size (default 50), so building it is
  cheap even with 5,000+ files loaded, and there's no per-card
  bookkeeping left that can fall out of sync with what's on screen.
  This is also the most likely cause of the sidebar-collapse freeze and
  the general page/view-switching lag, since the polling+internals-
  reaching-in system this replaces was the most fragile part of the UI.
- Verified via the same headless harness: import → clear → reimport,
  import across the old threshold, switching Expanded/Compact, and
  changing page size/navigating pages all now leave exactly one live
  card per visible file, no overlap, no missing cards.

### Compact View redesign
- Rebuilt to match spec exactly: thumbnail with its shorter edge fixed
  at 100px (aspect ratio preserved, longer edge capped so an extreme
  panorama/portrait can't blow out the layout) — bigger than Expanded's
  thumbnail, not smaller. Filename and file size stacked underneath it.
- No longer editable — no textboxes. Shows a short snippet of
  title/description with a character counter, the first 10 keywords
  with a total-count counter, generation status, and a Regenerate
  button.
- Grid columns are now auto-fit to the available window width in
  Compact (recomputed on resize) — the manual 1-4 column picker only
  applies to Expanded, per request.

### Other UI fixes
- Fixed a real gap between the title/description boxes and the
  keywords box in Expanded cards — the title/desc row was absorbing
  all of the card's leftover vertical space instead of the keywords
  row, leaving blank space above the keywords box.
- Save button was much wider than its icon+text needed — shrunk.
- Embed window now shows a live progress bar plus succeeded/failed/
  not-found counts at the bottom, matching the Generate tab's progress
  row, instead of only the Activity Log scrolling by.

### API Keys — Mistral (and every provider) mass-deactivation bug
- Found the actual cause of "adding a new API key deactivated every
  other active key for that provider": `_add_key` unconditionally set
  every existing key's `active` flag to False before adding the new
  one as active — wiping out the whole failover set the moment someone
  added one more key. Fixed: a new key now joins as active without
  touching any other key's state.
- Added "Activate All" / "Deactivate All" buttons under "Get API Key",
  per provider.

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
