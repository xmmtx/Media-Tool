# Media-Tool

多功能的**媒體管理器 & 批次重新命名工具**（PyQt6），前身是 YNRename 重新命名工具。
支援電影 / 劇集(番劇) / 音樂三類媒體的批次處理：重新命名、複製、硬連結，並自動比對
TMDB 中繼資料、識別字幕組、讀寫音樂標籤。

## 功能

- **電影批次處理**：FileBot 風格格式運算式（`{title_orig} ({year}) - {title_user} - [{group} {resolution}]`）→ 檔名解析（PTN/正規表示式）→ TMDB 比對（原名 + 本地化標題）→ 字幕組識別 → 輸出。
- **劇集 / 番劇批次處理**：`{title_orig} S{season_2d}E{episode_2d} {title_user} - [{group} {resolution}]`，季集補零，集標題取自 TMDB。
- **音樂批次處理**：mutagen 讀寫 ID3/FLAC 標籤；多藝術家自動拆分（`、` `/` `,`）；封面注入開關（圖片或從另一音樂檔複製）。
- **硬連結 + 跨磁碟回復**：優先 `os.link`，跨磁碟區（`EXDEV`）自動回復為複製並提示。
- **字幕組識別回復鏈**：本機 JSON 字典 → LLM 結構化輸出（OpenAI / Anthropic）→ 未設定則進入手動佇列。
- **手動介入佇列**：比對失敗的項目進入佇列，雙擊即可搜尋 TMDB/Bangumi 並套用比對重新處理；佇列持久化到 JSON，重新啟動可恢復。
- **可插拔提供者**：`BaseProvider` 抽象，TMDB 預設，Bangumi / MusicBrainz 已掛接。
- **跳過檢查（效能）**：運算式不含某佔位符時，跳過對應提取/API 步驟。
- **i18n**：簡體中文 / 繁體中文 / English（JSON 辭典）。
- **復原**：批次操作可整體復原（含硬連結/複製產物清理）。

## 環境需求

- Python 3.10+
- PyQt6
- mutagen
- ffprobe（隨 ffmpeg 安裝，用於實測影片解析度；缺失時解析度欄位為空，不影響其餘功能）

```text
pip install -r requirements.txt
```

## 執行

```text
python main.py
```

使用 Nix 開發環境：

```text
nix develop
python main.py
```

## 設定

- `src/db/config.json`：應用設定（TMDB API Key、LLM 端點/Key/模型、預設格式、跨磁碟回復策略、封面注入開關）。
- `src/db/subgroups.json`：字幕組辭典（每組含 `rename_to` 重新命名名與 `aliases` 可識別名）。
- 編輯後無需重新啟動即可被下次處理讀取；部分設定（如語言）在 UI 中直接切換並自動儲存。

## 目錄結構

```text
src/
├── core/
│   ├── formatters/       # FileBot 風格運算式求值（補零、跳過檢查）
│   ├── extractors/       # 檔名解析（PTN/正規表示式）、ffprobe 解析度
│   ├── providers/        # BaseProvider + TMDB / LLM / Bangumi / MusicBrainz
│   ├── metadata/         # mutagen 標籤讀寫、藝術家拆分、封面
│   ├── file_ops.py       # Rename / Copy / Hardlink（跨磁碟回復）/ Undo
│   └── processing.py     # 電影/劇集/音樂 pipeline + 手動佇列
├── ui/                   # PyQt6 主視窗、手動比對對話框、可重用元件
├── i18n/                 # zh_CN.json / zh_TW.json / en_US.json
└── db/                   # JSON 儲存：config / subgroups / manual_queue
```

## 測試

各模組透過臨時斷言腳本驗證（`QT_QPA_PLATFORM=offscreen` 可無頭測試 UI）：

```text
python -c "import sys; sys.path.insert(0,'src'); from core.processing import Processor; print('ok')"
```

## 授權

GNU General Public License v3.0（見 [LICENSE](LICENSE)）。
