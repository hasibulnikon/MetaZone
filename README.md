# Meta Zone

A Windows desktop app for stock-media metadata: AI-generated titles/
descriptions/keywords for images, and batch metadata embedding into
files via ExifTool.

## Structure

```
app.py              entry point
core/                constants, prefs, stateless helpers
engine/              AI provider calls + failover, prompt building, parsing
ui/                  theme, drag-and-drop, main window, dialogs, widgets
.github/workflows/   build.yml — builds MetaZone.exe and auto-releases
```

## Running from source

```
pip install -r requirements.txt
python app.py
```

## Versioning

The app version lives in one place: `APP_VERSION` in
`core/constants.py`. Bump it (v0.1 -> v0.2 -> ...) on each major
update — the build workflow reads it automatically and tags/releases
against it, so there's nothing to edit in `build.yml` itself.
