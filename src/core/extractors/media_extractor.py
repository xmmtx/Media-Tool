"""媒体信息提取器（Media Information Extractor）。

职责:
- **文件名解析**: 优先使用 anitopy（vendored 于 ``src/anitopy``，专为动漫
  命名设计，可剥离 ``[字幕组]`` 前缀并识别 ``- 01`` / ``EP01`` / ``第01话``
  等裸集号），其次内置正则，最后 PTN 回退，提取 title / year / season /
  episode / group / quality / codec。**分辨率绝不通过文件名判定**。
- **分辨率实测**: ``probe_resolution()`` 调用 ``ffprobe`` 读取真实视频流
  宽高（如 ``1920x1080``），是 ``resolution`` 字段的唯一来源；探测失败
  （无 ffprobe / 非视频 / 读取错误）时该字段保持 ``None``，绝不回退文件名推断。

输出统一的 :class:`MediaInfo` 数据对象，供 providers（TMDB 查询）与
expression_engine（格式化）消费。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

# 文件名里常见的“季/集/分辨率/质量”片段，用于从 title 中剔除
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_SEASON_RE = re.compile(r"[Ss](\d{1,2})")
_EPISODE_RE = re.compile(r"[Ee](\d{2,})")
_RESOLUTION_RE = re.compile(r"(\d{3,4})x(\d{3,4})|(\d{3,4})[pP]")
_QUALITY_RE = re.compile(
    r"(WEB[-.]?DL|BluRay|BDRip|HDRip|HDTV|DVDRip|REMUX|WEBRip|WEBrip)", re.I
)
_CODEC_RE = re.compile(r"[hx]\.?26[45]|HEVC|AVC|AV1", re.I)
_GROUP_RE = re.compile(r"[-\[(]\s*([^-\]()]+?)\s*[\]\s)]*$")

# extras 类型识别：文件名中的标记 → Jellyfin 剧集 extras 子目录（小写规范）
# 参考 Jellyfin 剧集 extras 目录: trailers / behind the scenes / featurettes /
# shorts / scenes / deleted scenes / interviews / samples / clips / extras
# 顺序敏感：先匹配更具体的（如 special scene → scenes），后兜底到 extras。
_EXTRAS_PATTERNS = [
    (r"trailers?|teasers?|\bcm\b|\bpv\b|web[\s\-_]*preview|web[\s\-_]*预告|预告", "trailers"),
    (r"behind\s+the\s+scenes|making\s+of|location\s+scouting|staff[\s\-_]*voice|"
     r"cast[\s\-_]*voice|disc[\s\-_]*menu|\bmenu\b|制作特辑|幕后|花絮", "behind the scenes"),
    (r"featurettes?|mini[\s\-_]*(?:drama|theater)|bd[\s\-_]*bonus|\bbonus\b|画集", "featurettes"),
    (r"shorts?|petit|spinoff|迷你|小剧场|短篇", "shorts"),
    (r"deleted\s+scen(?:e|es)|uncut|未放送|删减片段", "deleted scenes"),
    (r"special[\s\-_]*(?:scene|clip)|highlight|名场面|精彩片段|\bscenes?\b", "scenes"),
    (r"interviews?|cast[\s\-_]*(?:talk|interview)|stage[\s\-_]*greeting|舞台挨拶|采访", "interviews"),
    (r"samples?|试听|试看", "samples"),
    (r"clips?|短片|剪辑", "clips"),
    (r"ncop|nced|\bop\b|\bed\b|\bsp\b|tokuten|特典|特别篇|特别编", "extras"),
]


def _detect_extras(info: "MediaInfo", name: str) -> None:
    """在文件名中识别 extras 类型并写入 ``info.extra["extras"]``。"""
    for _pat, label in _EXTRAS_PATTERNS:
        if re.search(_pat, name, re.I):
            info.extra["extras"] = label
            return


# 已知 extras 类型，供外部校验
EXTRAS_TYPES = tuple(label for _pat, label in _EXTRAS_PATTERNS)


def _to_int(value: object) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class MediaInfo:
    """从文件名/媒体文件提取到的结构化信息。"""

    filename: str = ""
    path: str = ""
    title: Optional[str] = None
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    group: Optional[str] = None
    resolution: Optional[str] = None
    quality: Optional[str] = None
    codec: Optional[str] = None
    # 来源标记：title_source ∈ {anitopy, ptn, regex, none}
    # resolution_source ∈ {ffprobe, none}（分辨率仅由 ffprobe 实测，不回退文件名）
    title_source: str = "none"
    resolution_source: str = "none"
    extra: Dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        """转换为表达式引擎可直接消费的字典（空值剔除）。"""
        d: Dict[str, object] = {}
        for k in ("title", "year", "season", "episode", "group",
                  "resolution", "quality", "codec"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d


# ── PTN 加载（延迟、可选）─────────────────────────────────────────────────

_PTN_INSTANCE = None


def _load_ptn():
    """加载 PTN 解析器；优先已安装，其次尝试 reference 本地副本。"""
    global _PTN_INSTANCE
    if _PTN_INSTANCE is not None:
        return _PTN_INSTANCE
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            from PTN import PTN  # type: ignore

            _PTN_INSTANCE = PTN()
            return _PTN_INSTANCE
    except ImportError:
        pass
    ref = Path(__file__).resolve().parents[3] / "reference" / "parse-torrent-name"
    if ref.is_dir() and str(ref) not in sys.path:
        sys.path.insert(0, str(ref))
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                from PTN import PTN  # type: ignore

                _PTN_INSTANCE = PTN()
        except ImportError:
            _PTN_INSTANCE = None
    return _PTN_INSTANCE


# ── anitopy 加载与解析（第一优先）───────────────────────────────────────

_ANITOPY_MODULE = None


def _load_anitopy():
    """加载 anitopy 模块（vendored 于 ``src/anitopy``，或已 pip 安装）。"""
    global _ANITOPY_MODULE
    if _ANITOPY_MODULE is not None:
        return _ANITOPY_MODULE
    try:
        import anitopy  # type: ignore

        _ANITOPY_MODULE = anitopy
    except ImportError:
        _ANITOPY_MODULE = None
    return _ANITOPY_MODULE


def _strip_standalone_year(text: str):
    """提取独立年份片段（如 ``Movie Name 2020`` 中的 2020），返回清理后文本与年份。

    要求年份前后都不是字母/数字/汉字，避免误拆动漫标题中的数字。
    """
    m = re.search(
        r"(?<![A-Za-z0-9\u4e00-\u9fff])(19|20\d{2})(?![A-Za-z0-9\u4e00-\u9fff])",
        text,
    )
    if not m:
        return text, None
    cleaned = (text[: m.start()] + text[m.end():]).strip(" -_")
    return cleaned, int(m.group(1))


def _anitopy_parse(name: str) -> Optional[MediaInfo]:
    """用 anitopy 解析文件名并映射到 :class:`MediaInfo`。

    优势：专为动漫命名设计——剥离开头 ``[字幕组]`` / ``【组】``、识别
    ``- 01`` / ``EP01`` / ``[01]`` / ``第01话`` 等裸集号。
    """
    anitopy = _load_anitopy()
    if anitopy is None:
        return None
    try:
        parts = anitopy.parse(os.path.basename(name)) or {}
    except Exception:
        return None
    if not parts or not parts.get("anime_title"):
        return None
    title = str(parts["anime_title"]).strip()
    if not title:
        return None

    info = MediaInfo(filename=os.path.basename(name))
    info.title_source = "anitopy"
    info.title = title

    info.year = _to_int(parts.get("anime_year"))
    info.season = _to_int(parts.get("anime_season"))
    info.episode = _to_int(parts.get("episode_number"))
    info.group = (
        str(parts["release_group"]).strip() if parts.get("release_group") else None
    )

    # 分辨率只信 ffprobe：anitopy 的 video_resolution 仅存 extra 供参考
    if parts.get("video_resolution"):
        info.extra["filename_resolution"] = str(parts["video_resolution"]).strip()
    if parts.get("video_term"):
        info.codec = str(parts["video_term"]).strip()

    # quality：source（WebRip / BluRay / DVD…）优先；anitopy 有时把
    # ``WEB-DL`` 等误判为 episode_title，此时从 episode_title 回收
    quality = str(parts["source"]).strip() if parts.get("source") else None
    ep_title = str(parts["episode_title"]).strip() if parts.get("episode_title") else ""
    if not quality and _QUALITY_RE.search(ep_title):
        quality = ep_title
        ep_title = ""
    if quality:
        info.quality = quality
    if ep_title:
        info.extra["episode_title"] = ep_title

    for k in ("audio_term", "language", "subtitles", "other",
              "file_checksum", "release_version"):
        if parts.get(k):
            info.extra[k] = str(parts[k])

    # 年份后处理：anitopy 会把电影年份留在 title（如 ``Movie Name 2020``）
    title, y = _strip_standalone_year(title)
    if y is not None and info.year is None:
        info.year = y
    info.title = title or None

    # 单季动漫：有集数但无季标识（``- 01``）→ 默认第 1 季
    if info.episode is not None and info.season is None:
        info.season = 1

    return info if info.title else None


# ── 文件名解析 ────────────────────────────────────────────────────────────

def _regex_parse(name: str, fullname: str) -> MediaInfo:
    """内置正则解析器（PTN 不可用时的回退）。"""
    info = MediaInfo(filename=fullname)
    base = os.path.splitext(name)[0]
    used: list = []

    # 仅定位分辨率文本，用于 (1) 防止 "1920x1080" 中的 1920 被误认作年份、
    # (2) 从 title 中剔除。分辨率一律以 ffprobe 实测为准，不采信文件名。
    res_text = ""
    m = _RESOLUTION_RE.search(base)
    if m:
        res_text = m.group()
        used.append(res_text)

    year_base = base.replace(res_text, "", 1) if res_text else base
    m = _YEAR_RE.search(year_base)
    if m:
        info.year = int(m.group())
        used.append(m.group())
    m = _SEASON_RE.search(base)
    if m:
        info.season = int(m.group(1))
        used.append(m.group())
    m = _EPISODE_RE.search(base)
    if m:
        info.episode = int(m.group(1))
        used.append(m.group())
    for qm in _QUALITY_RE.finditer(base):
        if info.quality is None:
            info.quality = qm.group(1)
        used.append(qm.group())
    m = _CODEC_RE.search(base)
    if m:
        info.codec = m.group()
        used.append(m.group())

    # 尾部组名：形如 "-SweetSub" 或 "[SweetSub]"
    gm = _GROUP_RE.search(base)
    if gm:
        cand = gm.group(1).strip()
        if cand and not _RESOLUTION_RE.search(cand) and not _QUALITY_RE.search(cand):
            info.group = cand
            used.append(gm.group())

    # 识别 extras 标记（如 "Behind the Scenes" / "Trailers" / "Theme Music"），
    # 从 title 中剥离并记录类型（用于媒体库目录规范）
    for _pat, label in _EXTRAS_PATTERNS:
        m = re.search(_pat, base, re.I)
        if m:
            info.extra["extras"] = label
            used.append(m.group())
            break

    # title = 剔除所有已识别片段后清洗剩余文本（保留中文/日文）
    title = base
    for u in sorted(used, key=len, reverse=True):
        title = title.replace(u, "")
    title = re.sub(r"[\[\](){}_.\-]", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -")
    info.title = title or None
    return info


def extract_from_filename(name: str) -> MediaInfo:
    """从文件名解析媒体信息。

    策略（优先级）：
    1. anitopy（vendored 于 ``src/anitopy``）—— 专为动漫命名设计，
       剥离 ``[字幕组]`` 前缀并识别 ``- 01`` / ``EP01`` 等裸集号；
    2. 内置正则（对 CJK 与年份分离更稳定）；
    3. PTN（仅前两者都拿不到 title 时补充）。
    """
    base_name = os.path.basename(name)
    info = _anitopy_parse(base_name)
    if info and info.title:
        _detect_extras(info, base_name)
        return info
    info = _regex_parse(base_name, base_name)
    if info.title:
        info.title_source = "regex"
        _detect_extras(info, base_name)
        return info
    ptn = _load_ptn()
    if ptn is not None:
        try:
            parts = ptn.parse(os.path.splitext(base_name)[0]) or {}
            if parts.get("title"):
                info = MediaInfo(filename=base_name)
                info.title = str(parts["title"]).strip()
                info.title_source = "ptn"
                info.year = _to_int(parts.get("year"))
                info.season = _to_int(parts.get("season"))
                info.episode = _to_int(parts.get("episode"))
                info.group = str(parts["group"]).strip() if parts.get("group") else None
                info.quality = str(parts["quality"]) if parts.get("quality") else None
                info.codec = str(parts["codec"]) if parts.get("codec") else None
                _detect_extras(info, base_name)
                return info
        except Exception:
            pass  # PTN 解析失败时保留正则结果
    _detect_extras(info, base_name)
    return info


# ── 分辨率实测（ffprobe）──────────────────────────────────────────────────

def probe_resolution(path: str, timeout: float = 5.0) -> Optional[str]:
    """用 ffprobe 读取第一个视频流的实际分辨率，如 ``1920x1080``。

    任何失败（ffprobe 不存在 / 文件不存在 / 非视频 / 超时）均返回 ``None``。
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json", path,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        streams = data.get("streams") or []
        if not streams:
            return None
        width = streams[0].get("width")
        height = streams[0].get("height")
        if width and height:
            return f"{width}x{height}"
    except (FileNotFoundError, subprocess.SubprocessError, ValueError,
            OSError, json.JSONDecodeError):
        pass
    return None


def extract_media_info(path: str, prefer_probe: bool = True) -> MediaInfo:
    """从文件路径提取完整媒体信息。

    - 分辨率字段**只**来自 ffprobe 实测；探测失败时保持 ``None``，
      不会用文件名推断（``prefer_probe`` 仅控制是否尝试实测，保留以便
      调用方跳过 IO 探测）。
    """
    info = extract_from_filename(path)
    info.path = path
    if prefer_probe:
        real = probe_resolution(path)
        if real:
            info.resolution = real
            info.resolution_source = "ffprobe"
        else:
            info.resolution_source = "none"
    else:
        info.resolution_source = "none"
    return info
