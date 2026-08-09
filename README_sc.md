# Media-Tool

多功能的**媒体管理器 & 批量重命名工具**（PyQt6），前身是 YNRename 重命名工具。
支持电影 / 剧集(番剧) / 音乐三类媒体的批处理：重命名、复制、硬链接，并自动匹配
TMDB 元数据、识别字幕组、读写音乐标签。

## 功能

- **电影批处理**：FileBot 风格格式表达式（`{title_orig} ({year}) - {title_user} - [{group} {resolution}]`）→ 文件名解析（PTN/正则）→ TMDB 匹配（原名 + 本地化标题）→ 字幕组识别 → 输出。
- **剧集 / 番剧批处理**：`{title_orig} S{season_2d}E{episode_2d} {title_user} - [{group} {resolution}]`，季集补零，集标题取自 TMDB。
- **音乐批处理**：mutagen 读写 ID3/FLAC 标签；多艺术家自动拆分（`、` `/` `,`）；封面注入开关（图片或从另一音乐文件复制）。
- **硬链接 + 跨盘回退**：优先 `os.link`，跨卷（`EXDEV`）自动回退为复制并提示。
- **字幕组识别回退链**：本地 JSON 字典 → LLM 结构化输出（OpenAI / Anthropic）→ 未配置则进入手动队列。
- **手动干预队列**：匹配失败的项进入队列，双击即可搜索 TMDB/Bangumi 并应用匹配重新处理；队列持久化到 JSON，重启可恢复。
- **可插拔提供者**：`BaseProvider` 抽象，TMDB 默认，Bangumi / MusicBrainz 已挂接。
- **跳过检查（性能）**：表达式不含某占位符时，跳过对应提取/API 步骤。
- **i18n**：简体中文 / 繁体中文 / English（JSON 词典）。
- **撤销**：批量操作可整体撤销（含硬链接/复制产物清理）。

## 环境要求

- Python 3.10+
- PyQt6
- mutagen
- ffprobe（随 ffmpeg 安装，用于实测视频分辨率；缺失时分辨率字段为空，不影响其余功能）

```text
pip install -r requirements.txt
```

## 运行

```text
python main.py
```

使用 Nix 开发环境：

```text
nix develop
python main.py
```

## 配置

- `src/db/config.json`：应用配置（TMDB API Key、LLM 端点/Key/模型、默认格式、跨盘回退策略、封面注入开关）。
- `src/db/subgroups.json`：字幕组字典（每组含 `rename_to` 重命名名与 `aliases` 可识别名）。
- 编辑后无需重启即可被下次处理读取；部分设置（如语言）在 UI 中直接切换并自动保存。

## 目录结构

```text
src/
├── core/
│   ├── formatters/       # FileBot 风格表达式求值（补零、跳过检查）
│   ├── extractors/       # 文件名解析（PTN/正则）、ffprobe 分辨率
│   ├── providers/        # BaseProvider + TMDB / LLM / Bangumi / MusicBrainz
│   ├── metadata/         # mutagen 标签读写、艺术家拆分、封面
│   ├── file_ops.py       # Rename / Copy / Hardlink（跨盘回退）/ Undo
│   └── processing.py     # 电影/剧集/音乐 pipeline + 手动队列
├── ui/                   # PyQt6 主窗口、手动匹配对话框、可复用组件
├── i18n/                 # zh_CN.json / zh_TW.json / en_US.json
└── db/                   # JSON 存储：config / subgroups / manual_queue
```

## 测试

各模块通过临时断言脚本验证（`QT_QPA_PLATFORM=offscreen` 可无头测试 UI）：

```text
python -c "import sys; sys.path.insert(0,'src'); from core.processing import Processor; print('ok')"
```

## 许可证

GNU General Public License v3.0（见 [LICENSE](LICENSE)）。
