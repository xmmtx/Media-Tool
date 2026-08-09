# AGENTS.md

PyQt6 media manager & batch renamer (fork of YNRename). Core logic lives in `src/`; the entry point is `main.py`, which adds `src/` to `sys.path` automatically. Deps: `pip install -r requirements.txt`.

## Common commands

- Run: `python main.py`
- Headless UI test: `QT_QPA_PLATFORM=offscreen`

## Architecture (deep modules, one responsibility each)

- `core/formatters/expression_engine.py` — FileBot-style expression evaluation. `required_fields(fmt)` returns the fields an expression needs; the **skip-check** pattern (don't run extraction/API steps for unused placeholders) drives every pipeline. Keep new steps on the same pattern.
- `core/extractors/media_extractor.py` — filename parsing (built-in regex first, PTN only as fallback) + ffprobe resolution. **Resolution comes solely from ffprobe** — never trust the filename.
- `core/providers/` — `BaseProvider` abstraction; `PROVIDERS` registry + `get_provider(name)`. A new provider implements `BaseProvider.search()` and registers itself.
- `core/metadata/music_tags.py` — mutagen tags; multiple artists are split via `split_artists` and written back as a multi-value tag.
- `core/file_ops.py` — Rename / Copy / Hardlink (cross-device `EXDEV` auto-falls-back to copy, flagged via `fallback_used`) + undo history.
- `core/processing.py` — movie/TV/music pipelines + manual queue. `process_file()` returns `QueueItem(status=ok|manual|error)`; manual items persist to JSON and can be `reprocess()`ed after human correction.
- `ui/main_window.py` — PyQt6 main window + `ProcessingThread` (background thread updates the table via signals). Call `enable_high_dpi()` before creating `QApplication`.

## Conventions

- Cross-package imports use **top-level module names** (`from core.…`, `from db.…`, `from i18n.…`) — the project runs with `src/` on `sys.path`. Do not use `..` relative imports that escape the top-level package.
- `src/db/config.json` (holds API keys), `subgroups.json`, `manual_queue.json` are **local runtime data, gitignored** — never commit them.
- New UI text must be added to all three `src/i18n/` dictionaries (zh_CN / zh_TW / en_US) with matching keys.
- Testing: no pytest harness; use throwaway assert scripts (set `QT_QPA_PLATFORM=offscreen` for UI) and delete them after running.
- External services (TMDB / LLM / Bangumi / MusicBrainz) are called with the stdlib `urllib` only — do not add third-party HTTP dependencies.
