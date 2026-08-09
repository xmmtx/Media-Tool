# Media-Tool

A multi-functional **media manager & batch renamer** (PyQt6), forked from the YNRename
renaming tool. It batch-processes **movies**, **TV shows / anime**, and **music** with
rename / copy / hardlink operations, automatic TMDB metadata matching, subtitle-group
recognition, and music tag read/write.

> 简体中文版见 [README_sc.md](README_sc.md) · 繁體中文版見 [README_tc.md](README_tc.md)

## Features

- **Movie batch processing**: FileBot-style format expressions (e.g. `{title_orig} ({year}) - {title_user} - [{group} {resolution}]`) → filename parsing (PTN/regex) → TMDB matching (original + localized titles) → subtitle-group recognition → output.
- **TV / anime batch processing**: `{title_orig} S{season_2d}E{episode_2d} {title_user} - [{group} {resolution}]`, season/episode zero-padding, episode titles from TMDB.
- **Music batch processing**: mutagen reads/writes ID3/FLAC tags; multiple artists are split automatically (`、` `/` `,`); cover-art injection toggle (from an image file or copied from another music file).
- **Hardlink with cross-drive fallback**: prefers `os.link`; on a cross-volume `EXDEV` it automatically falls back to copying and flags the result.
- **Subtitle-group fallback chain**: local JSON dictionary → LLM structured output (OpenAI / Anthropic) → manual queue when unconfigured.
- **Manual intervention queue**: failed items land in a queue; double-click to search TMDB/Bangumi and apply a match for reprocessing; the queue persists to JSON and restores on restart.
- **Pluggable providers**: `BaseProvider` abstraction; TMDB by default, with Bangumi and MusicBrainz attached.
- **Skip-check (performance)**: if the format expression omits a placeholder, the corresponding extraction/API step is skipped.
- **i18n**: Simplified Chinese / Traditional Chinese / English (JSON dictionaries).
- **Undo**: batch operations can be undone as a whole (including hardlink/copy artifacts).

## Requirements

- Python 3.10+
- PyQt6
- mutagen
- ffprobe (ships with ffmpeg; used to measure real video resolution. When missing, the resolution field stays empty and everything else still works.)

```text
pip install -r requirements.txt
```

## Run

```text
python main.py
```

With the Nix dev shell:

```text
nix develop
python main.py
```

## Configuration

- `src/db/config.json` — app config (TMDB API key, LLM endpoint/key/model, default formats, cross-drive fallback policy, cover-inject toggle).
- `src/db/subgroups.json` — subtitle-group dictionary (each entry has `rename_to` for the final name and `aliases` for recognition).
- Most settings are picked up on the next run; some (like language) switch live in the UI and save automatically.

## Project layout

```text
src/
├── core/
│   ├── formatters/       # FileBot-style expression evaluation (padding, skip-check)
│   ├── extractors/       # Filename parsing (PTN/regex), ffprobe resolution
│   ├── providers/        # BaseProvider + TMDB / LLM / Bangumi / MusicBrainz
│   ├── metadata/         # mutagen tag read/write, artist split, cover art
│   ├── file_ops.py       # Rename / Copy / Hardlink (cross-drive fallback) / Undo
│   └── processing.py     # Movie/TV/music pipelines + manual queue
├── ui/                   # PyQt6 main window, manual-match dialog, reusable widgets
├── i18n/                 # zh_CN.json / zh_TW.json / en_US.json
└── db/                   # JSON stores: config / subgroups / manual_queue
```

## Tests

Modules are verified with throwaway assert scripts (`QT_QPA_PLATFORM=offscreen` enables headless UI tests):

```text
python -c "import sys; sys.path.insert(0,'src'); from core.processing import Processor; print('ok')"
```

## License

GNU General Public License v3.0 (see [LICENSE](LICENSE)).
