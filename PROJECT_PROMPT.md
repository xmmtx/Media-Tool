# Role & Project Overview
You are an expert Python Developer specializing in Desktop Media Management software. 
We are refactoring and extending THIS current repository (which is a fork of `YNRename`) into a multi-functional Media Manager & Batch Renamer tool.

## Codebase Context
- You are working directly inside the `YNRename` codebase.
- Preserve and reuse existing UI components, queue system, thread pools, and undo mechanics where appropriate.
---

## Core Architecture & Technical Implementation Guidelines

1. **Language & High-DPI UI**:
   - Python 3.10+, PyQt6 (or PySide6).
   - Enforce High-DPI auto-scaling in the `main.py` entry point:
     ```python
     QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
         Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
     )
     ```
2. **Localization (i18n)**:
   - Support Simplified Chinese (`zh_CN`), Traditional Chinese (`zh_TW`), and English (`en_US`).
   - Use a lightweight, modular JSON-based dictionary approach for easy translation expansion.
3. **File Operations & Hardlink Resilience**:
   - Primary file operations: Rename, Copy, Hardlink (`os.link(src, dst)`).
   - **Cross-Drive Hardlink Strategy**: Since hardlinks cannot span across different disk volumes/drives on Windows, catch `OSError` (specifically errno 17 / Cross-device link) and automatically fallback to Copying or issue a friendly warning in UI.
4. **Reference Codebase**: Follow and reuse the core architecture of `reference/YNRename/` for queue management, threading, and undo history.

---

## Feature Specifications

### Module 1: Movie Batch Processor (Rename, Hardlink, Copy)
- **Formatting Engine**: Evaluate FileBot-like custom expressions (refer to `reference/Format Expressions.html`).
  - *Custom Multilingual Expression Extension*: Support tags like `{title_orig}` (Original Title), `{title_user}` (User Native Language Title from TMDB), `{year}`, `{group}`, `{resolution}`.
  - *Example Format*: `{title_orig} ({year}) - {title_user} - [{group} {resolution}]`
- **Metadata & Resolution Extraction**:
  - Auto-extract film name & year from filename using PTN / regex.
  - Auto-extract **real video resolution** (e.g., `1920x1080`, `3840x2160`) via `pymediainfo` or `ffprobe` rather than relying solely on filename strings.
- **TMDB Integration**: Query TMDB Movie API with extracted name/year.
- **Subgroup (字幕组) Recognition & Agent Fallback**:
  - Maintain a local **JSON database** (`src/db/subgroups.json`) of recognized release groups (see `SubgroupStore`).
  - Each group entry stores `rename_to` (the canonical name written into filenames) and `aliases` (recognizable names for matching, e.g. Chinese/English variants). `recognize()` matches against aliases + key + rename_to; formatting uses `display_name()` → `rename_to`.
  - **Fallback**: If subgroup fails recognition -> Send raw filename to Configured Agent LLM API (e.g. OpenAI/Claude API) using Structured Outputs (JSON Schema, e.g., `{"subgroup": "NekomoeKissaten"}`) to parse the group name, and dynamically save the new group to the local JSON DB (`source: "llm"`). If no API key configured -> Push file to "Manual Intervention Queue".
  - **App Configuration**: All API keys & settings (TMDB key, LLM endpoint/key/model, default formats, file-ops fallback, cover-inject toggle) live in a separate JSON config file (`src/db/config.json`, see `ConfigStore`), editable without code changes.
- **Execution**: On match failure (Movie Name or Year not found), skip file and move to **Manual Intervention Queue**.
- **Performance Optimization**: If custom format expression DOES NOT contain specific placeholders (e.g., `{resolution}` or `{group}`), skip those extraction/API steps entirely to optimize performance.

### Module 2: TV Show / Anime Batch Processor (Rename, Hardlink, Copy)
- **Formatting Engine**: Extend expression parser for TV shows.
  - *Example Format*: `{title_orig} S{season_2d}E{episode_2d} {title_user} - [{group} {resolution}]`
  - Example Output: `うまよん S01E01 40分以下！特雷森学园补考特别节目 - [NekomoeKissaten 1920x1080]`
- **Metadata Extraction**:
  - Extract TV Title, Year, Season (`Sxx`), Episode (`Exx`) using regex or `AnitomyPy` logic.
  - Extract actual video resolution via MediaInfo/ffprobe analysis.
- **TMDB TV API**: Search TMDB for Show & Episode titles (original & localized title).
- **Fallback**: If TV Title or Season/Episode parsing fails -> Move to **Manual Intervention Queue**. Subgroup parsing failure follows the same LLM Agent / Manual queue fallback logic as Module 1.

### Module 3: Music Batch Processor (Rename, Modify Metadata, Hardlink, Copy)
- **Metadata Reading/Writing**: Use `mutagen` to inspect and write ID3/FLAC metadata.
- **Attributes Processing**:
  - Extract Song Title & Artist(s) from audio file ID3 tags.
  - **Artist Splitting**: Automatically split multiple artists separated by Chinese enumeration comma (`、`), slash (`/`), or `,`, and update the file ID3 `ARTIST` / `ARTISTS` tags appropriately (e.g., converting "歌手A、歌手B" to distinct artist tags so media libraries index them properly).
- **Album Cover Management**:
  - Support manual album cover image import (or copying cover art from another music file).
  - Provide an **ON/OFF toggle switch** next to the "Start Processing" button for cover art injection.
- **Fallback**: If Song Title or Artist missing in metadata -> Move to **Manual Intervention Queue**.

### Module 4: Manual Intervention UI (TMDB & Provider Inspector)
- **Design**:
  - Friendly Search Bar with live search suggestions.
  - Search Result List with poster cards, metadata preview, and filtering options (by year, media type, language).
  - Smooth "Apply Match" action logic to push corrected TMDB/Provider data back into the failed queue items in Module 1, 2, or 3 for immediate reprocessing.
- **Extensible Providers**: Abstract provider interface (`BaseProvider`) so TMDB is the default primary, but secondary APIs (e.g., Bangumi, MusicBrainz) can be attached seamlessly.

---

## Directory Structure Plan
Please organize code clearly as follows:
```text
src/
├── core/
│   ├── formatters/       # Expression evaluator (FileBot style parser)
│   ├── extractors/       # Filename, PTN, Anitomy, MediaInfo parsers
│   ├── providers/        # TMDB, LLM Agent (JSON Schema), MusicBrainz API clients
│   ├── metadata/         # Mutagen ID3 tag & artist splitter logic
│   └── file_ops.py       # Rename, Hardlink (with fallback), Copy, Undo history
├── ui/
│   ├── main_window.py    # PyQt6 Main Shell with High-DPI & Theme
│   ├── manual_match.py   # Search & manual match dialog/view
│   └── components/       # Reusable tables, toggle switches, preview boxes
├── i18n/                 # zh_CN.json, zh_TW.json, en_US.json
└── db/                   # JSON-based local storage (no SQLite)
    ├── json_store.py     # Atomic-read/write base class (thread-safe)
    ├── subgroup_store.py # Subgroup dictionary store
    ├── config_store.py   # App config store (dot-path access)
    ├── subgroups.json    # Subgroup dictionary data (auto-managed)
    └── config.json       # App config (API keys etc., user-editable)

```

## First Task Instruction

Please start by generating the **Core Expression Engine (`src/core/formatters/expression_engine.py`)** and **Media Information Extractor (`src/core/extractors/media_extractor.py`)**.
Ensure the Expression Engine supports tokens like `{title_orig}`, `{title_user}`, `{season_2d}`, `{episode_2d}`, `{group}`, `{resolution}`, and provides performance checks to skip unused steps.