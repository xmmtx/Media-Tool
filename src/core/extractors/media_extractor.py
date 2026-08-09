"""媒体信息提取器（Media Information Extractor）。

职责:
- **文件名解析**: 优先使用 PTN（`reference/parse-torrent-name`，若可导入），
  否则回退到内置正则解析器，提取 title / year / season / episode / group /
  quality / codec。**分辨率绝不通过文件名判定**。
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
    # 来源标记：title_source ∈ {ptn, regex, none}
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

    策略：内置正则优先（对 CJK 与年份分离更稳定）；仅当正则解析不到
    ``title`` 时，才尝试 PTN 作为补充解析器。
    """
    base_name = os.path.basename(name)
    info = _regex_parse(base_name, base_name)
    if info.title:
        info.title_source = "regex"
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
                return info
        except Exception:
            pass  # PTN 解析失败时保留正则结果
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
