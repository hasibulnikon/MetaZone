# Changelog

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
