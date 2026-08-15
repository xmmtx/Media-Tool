"""批处理编排：电影 / 剧集 / 音乐三条 pipeline + 手动干预队列。

把 extractors / providers / metadata / formatters / db / file_ops 串成一条
可批量执行的流水线。每个文件经 :meth:`Processor.process_file` 处理并归入状态：

- ``ok``     : 处理成功（生成新文件/重命名）
- ``manual`` : 需要人工干预（TMDB 匹配失败 / 组识别失败 / 标签缺失），
               进入 ``manual_queue``，可由 Module 4 UI 修正后 ``reprocess``
- ``error``  : 运行期错误（源缺失 / 目标冲突 / 文件操作失败）
- ``pending``: 尚未处理

**性能优化（跳过检查）**：依据表达式 ``required_fields()`` 决定是否执行
分辨率实测 / 字幕组识别 / TMDB 查询 / 季集解析，未用到的步骤全部跳过。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("core.processing")

# 字幕文件扩展名（跟随视频自动改名/移动）
SUBTITLE_EXTS = {".ass", ".srt", ".ssa", ".vtt"}

# 视频 / 音频扩展名（拖拽文件夹时仅收集可处理的媒体文件）
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm",
              ".m4v", ".ts", ".m2ts", ".rmvb", ".rm", ".mpg", ".mpeg",
              ".vob", ".3gp", ".ogv", ".asf", ".divx", ".m2v"}
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".aac", ".m4a", ".ogg", ".opus",
              ".wma", ".ape", ".aiff", ".amr", ".alac"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS | SUBTITLE_EXTS

# 字幕语言标签规范化：常见中文标签 → Jellyfin 式“简中/繁中”
_SUBTITLE_LANG_NORM = {
    "sc": "简中", "chs": "简中", "scjp": "简中", "简体": "简中",
    "简中": "简中", "zh-hans": "简中", "zh_cn": "简中",
    "tc": "繁中", "cht": "繁中", "tcjp": "繁中", "繁體": "繁中",
    "繁中": "繁中", "zh-hant": "繁中", "zh_tw": "繁中", "big5": "繁中",
}

try:
    from ..db import ConfigStore, ManualQueueStore, SubgroupStore  # src 作为顶层包运行时
    from ..db.subgroup_store import normalize as _norm_group_name
except ImportError:  # src 作为 sys.path 根运行时
    from db import ConfigStore, ManualQueueStore, SubgroupStore  # type: ignore
    from db.subgroup_store import normalize as _norm_group_name  # type: ignore
from .extractors.media_extractor import (
    MediaInfo,
    extract_from_filename,
    probe_resolution,
)
from .file_ops import FileOperator, OperationResult
from .formatters.expression_engine import evaluate, required_fields
from .localize import (
    convert_title,
    looks_localized,
    title_language_chain,
    tmdb_lang,
)
from .metadata.music_tags import (
    copy_cover,
    inject_cover,
    read_music_tags,
    split_artists,
    update_music_tags,
)
from .metadata.music_unlock import KUGOU_EXTS, UnlockError, decrypt_kugou

# 酷狗加密音频（.kgm/.vpr/.kgg/.kgma）纳入媒体收集。注意 MEDIA_EXTS 是
# 模块顶部 union 出来的拷贝，必须在此一并追加，否则拖拽收集（is_media）漏判。
AUDIO_EXTS |= KUGOU_EXTS
MEDIA_EXTS |= KUGOU_EXTS
from .providers import LlmSubgroupProvider, MediaMatch, TMDBProvider

# Windows 非法文件名字符（含控制符）
_INVALID_WIN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 特典“视频名片段”规范化：已知缩写保持大写，其余单词标题化
_EXTRAS_ACRONYMS = {"PV", "CM", "NCOP", "NCED", "OP", "ED", "SP", "BD", "BTS"}
_EXTRAS_SMALL_WORDS = {"of", "the", "and", "a", "an", "in", "on", "to",
                       "for", "at", "by", "with", "vs"}

# 特典文件名中的“视频详情”片段（分辨率/来源/编码/位深），命名清洗时剔除
_EXTRAS_DETAIL_RE = re.compile(
    r"(?i)\b\d{3,4}x\d{3,4}\b|\b\d{3,4}p\b|\b\d{1,2}bit\b|"
    r"\b(web[-.]?dl|bluray|bdrip|hdrip|hdtv|dvdrip|remux|webrip)\b|"
    r"\b(h\.?264|h\.?265|hevc|avc|av1)\b"
)


def _norm_extras_fragment(frag: str) -> str:
    """规范化特典“视频名片段”：缩写保持大写，其余标题化，去分隔符噪音。

    如 ``making of`` → ``Making of``、``pv`` → ``PV``、``ncop`` → ``NCOP``。
    """
    frag = re.sub(r"[\[\](){}_.\-]", " ", frag)
    frag = re.sub(r"\s+", " ", frag).strip(" -")
    if not frag:
        return ""
    if frag.upper() in _EXTRAS_ACRONYMS:
        return frag.upper()
    words = []
    for i, w in enumerate(frag.split(" ")):
        if not w:
            continue
        low = w.lower()
        if i > 0 and low in _EXTRAS_SMALL_WORDS:
            words.append(low)
        else:
            words.append(w[:1].upper() + w[1:])
    return " ".join(words)


def sanitize_filename(name: str) -> str:
    """把生成的文件名清理为各平台安全形式（非法字符→下划线，去首尾点/空格）。"""
    name = _INVALID_WIN.sub("_", name)
    return name.strip(" .")


# 剧集类型分类：TMDB genre_ids（用于媒体库模式选择番剧/电视剧/纪录片根目录）
_ANIME_GENRE_IDS = {16}   # Animation → 番剧
_DOC_GENRE_IDS = {99}     # Documentary → 纪录片


def classify_tv_type(genre_ids) -> Optional[str]:
    """按 TMDB 类型 ID 分类剧集：``'anime'`` | ``'drama'`` | ``'doc'``。

    无任何类型信息（如 TMDB 未匹配或未返回 genre_ids）时返回 ``None``，
    调用方据此把该文件送入人工队列。
    """
    g = set(genre_ids or [])
    if not g:
        return None
    if g & _DOC_GENRE_IDS:
        return "doc"
    if g & _ANIME_GENRE_IDS:
        return "anime"
    return "drama"


@dataclass
class ProcessingOptions:
    """单文件处理参数。"""

    kind: str                    # movie | tv | music
    format: str                  # 表达式（如 "{title_orig} ({year}) - [{group}]"）
    mode: str = "rename"         # rename | copy | hardlink
    output_dir: Optional[str] = None  # 自定义模式目标目录（None = 源目录）
    output_mode: str = "custom"  # custom | library（媒体库 Jellyfin 结构）
    library_roots: Optional[Dict[str, str]] = None  # {"movie","tv","music"} 根目录
    language: str = "zh-CN"
    inject_cover: bool = False   # 音乐：是否注入封面
    cover_path: Optional[str] = None  # 封面图片路径，或源音乐文件路径（自动识别）
    dry_run: bool = False       # 匹配阶段：只算目标名/路径，不执行文件操作


@dataclass
class QueueItem:
    """一个待处理/已处理文件项。"""

    path: str
    kind: str
    status: str = "pending"      # pending | ok | manual | error
    info: Optional[MediaInfo] = None
    match: Optional[MediaMatch] = None
    group: Optional[str] = None  # 规范组名（rename_to）
    format: str = ""             # 本次使用的表达式（reprocess 时复用）
    new_name: str = ""
    dst: Optional[str] = None    # 匹配阶段计算出的目标路径（执行阶段使用）
    error: str = ""
    reason: str = ""             # manual 原因
    source_path: str = ""        # 处理前原始路径（解密等变换后供 UI 定位行）

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "kind": self.kind,
            "status": self.status,
            "format": self.format,
            "new_name": self.new_name,
            "error": self.error,
            "reason": self.reason,
            "group": self.group,
        }


class Processor:
    """批处理编排器（持有配置/存储/提供者/文件操作器）。"""

    def __init__(self, config=None, subgroup_store=None, operator=None,
                 language: Optional[str] = None, manual_store=None) -> None:
        self.config = config or ConfigStore()
        self.subgroups = subgroup_store or SubgroupStore()
        self.operator = operator or FileOperator(
            fallback=str(self.config.get("file_ops.fallback_on_cross_device", "copy") or "copy")
        )
        self.language = language or str(self.config.get("language", "zh-CN") or "zh-CN")
        self.tmdb = TMDBProvider(self.config)
        self.llm = LlmSubgroupProvider(self.config)
        self.manual_store = manual_store if manual_store is not None else ManualQueueStore()
        self.manual_queue: List[QueueItem] = []
        self._video_by_ep: Dict[tuple, QueueItem] = {}  # (season, episode) -> 已匹配视频
        self._extras_seq: Dict[tuple, int] = {}  # (目标目录, 视频名片段) -> 已分配序号

    @staticmethod
    def is_subtitle(path: str) -> bool:
        """是否字幕文件（扩展名判断）。"""
        return os.path.splitext(path)[1].lower() in SUBTITLE_EXTS

    @staticmethod
    def is_media(path: str) -> bool:
        """是否可处理的媒体文件（视频/音频/字幕），供拖拽文件夹时过滤非媒体文件。"""
        return os.path.splitext(path)[1].lower() in MEDIA_EXTS

    def reset_match_cache(self) -> None:
        """清空本批次视频匹配缓存（开始新一批匹配前调用）。"""
        self._video_by_ep.clear()
        self._extras_seq.clear()

    def _record_video(self, item: QueueItem) -> None:
        """视频匹配成功后登记 (season, episode) → item，供字幕跟随。"""
        if item.status == "ok" and item.info \
                and item.info.season is not None and item.info.episode is not None \
                and not (item.info.extra or {}).get("extras"):
            self._video_by_ep[(item.info.season, item.info.episode)] = item

    # ── 对外入口 ──────────────────────────────────────────────────────────

    def process_file(
        self,
        path: str,
        options: ProcessingOptions,
        forced_match: Optional[MediaMatch] = None,
        forced_group: Optional[str] = None,
        forced_values: Optional[Dict[str, object]] = None,
    ) -> QueueItem:
        """处理单个文件。

        - ``forced_match`` / ``forced_group`` 供手动干预后重跑。
        - ``forced_values`` 供手动匹配：直接以预填字段（如集标题/季/集）
          走格式化，跳过 TMDB 搜索与分类。
        """
        item = QueueItem(path=path, kind=options.kind)
        item.format = options.format
        logger.info("处理文件: %s (kind=%s, mode=%s)", path, options.kind, options.mode)
        try:
            if self.is_subtitle(path):
                return self._process_subtitle(item, options)
            if options.kind == "movie":
                result = self._process_movie(item, options, forced_match, forced_group)
                self._record_video(result)
                return result
            if options.kind == "tv":
                result = self._process_tv(item, options, forced_match, forced_group,
                                          forced_values)
                self._record_video(result)
                return result
            if options.kind == "music":
                return self._process_music(item, options)
        except Exception as e:  # 运行期错误不中断整个批次
            item.status = "error"
            item.error = str(e)
            logger.error("处理异常 %s: %s", path, e)
        return item

    def reprocess(self, item: QueueItem, forced_match: Optional[MediaMatch] = None,
                  forced_group: Optional[str] = None) -> QueueItem:
        """把处于 manual 状态的项移出队列并用修正数据重新处理。"""
    def reprocess(self, item: QueueItem, forced_match: Optional[MediaMatch] = None,
                  forced_group: Optional[str] = None) -> QueueItem:
        """把处于 manual 状态的项移出队列并用修正数据重新处理。"""
        self.manual_queue = [i for i in self.manual_queue if i is not item]
        self._persist_manual()
        options = ProcessingOptions(kind=item.kind, format=item.format or "")
        return self.process_file(item.path, options, forced_match, forced_group)

    # ── 电影 pipeline ─────────────────────────────────────────────────────

    def _process_movie(self, item, options, forced_match=None, forced_group=None) -> QueueItem:
        needed = required_fields(options.format)
        is_library = options.output_mode == "library"
        info = extract_from_filename(item.path)
        item.info = info
        logger.info("文件名解析: title=%s year=%s", info.title, info.year)
        # 特典文件（Featurettes/Behind the Scenes 等子文件夹）：不当作正片电影
        if (info.extra or {}).get("extras"):
            return self._process_extras(item, options)

        group = forced_group
        if ("group" in needed or is_library) and not group:
            groups = self.resolve_subgroups(item.path, info.group)
            group = "&".join(groups) if groups else None
            item.group = group
            if not group:
                return self._to_manual(item, "subgroup not recognized")

        if "resolution" in needed or is_library:
            info.resolution = probe_resolution(item.path)
            logger.info("分辨率(ffprobe): %s", info.resolution)

        match = forced_match
        if not match and ({"title_orig", "title_user", "year"} & needed or is_library):
            if not info.title:
                return self._to_manual(item, "cannot parse title from filename")
            matches = self.tmdb.search(info.title, year=info.year,
                                       media_type="movie",
                                       language=tmdb_lang(options.language))
            logger.info("TMDB 搜索 %s (year=%s): %d 个候选",
                        info.title, info.year, len(matches))
            match = self._pick_match(matches, info.year)
            if match:
                logger.info("TMDB 选中: %s (%s) id=%s",
                            match.title_orig, match.title_user, match.tmdb_id)
            if not match:
                return self._to_manual(item, "no TMDB movie match")
        item.match = match

        values = self._base_values(info, group, match)
        if match and ("title_user" in needed or is_library):
            values["title_user"] = self._resolve_title_user(
                match, "movie", options.language)
        return self._finalize(item, options, values)

    # ── 剧集 pipeline ─────────────────────────────────────────────────────

    def _process_tv(self, item, options, forced_match=None, forced_group=None,
                    forced_values: Optional[Dict[str, object]] = None) -> QueueItem:
        needed = required_fields(options.format)
        info = extract_from_filename(item.path)
        item.info = info
        logger.info("文件名解析: title=%s year=%s season=%s episode=%s",
                    info.title, info.year, info.season, info.episode)
        # 特典文件（NCOP/NCED/PV/menu/Tokuten 等）：识别为 extras，不当作正片集
        if (info.extra or {}).get("extras"):
            return self._process_extras(item, options)

        # 手动匹配：直接用预填字段（集标题/季/集）走格式化，跳过 TMDB 搜索与分类
        is_library = options.output_mode == "library"
        if forced_values is not None:
            values = dict(forced_values)
            logger.info("手动匹配预填值: %s", values)
            if "group" in needed or is_library:
                group = forced_group
                if not group:
                    groups = self.resolve_subgroups(item.path, info.group)
                    group = "&".join(groups) if groups else None
                item.group = group
                if group:
                    values["group"] = group
            if "resolution" in needed or is_library:
                info.resolution = probe_resolution(item.path)
                if info.resolution:
                    values["resolution"] = info.resolution
            if info.year and "year" not in values:
                values["year"] = info.year
            return self._finalize(item, options, values)

        if ({"season", "episode"} & needed or is_library) \
                and (info.season is None or info.episode is None):
            return self._to_manual(item, "cannot parse season/episode")

        group = forced_group
        if ("group" in needed or is_library) and not group:
            groups = self.resolve_subgroups(item.path, info.group)
            group = "&".join(groups) if groups else None
            item.group = group
            if not group:
                return self._to_manual(item, "subgroup not recognized")

        if "resolution" in needed or is_library:
            info.resolution = probe_resolution(item.path)

        # 媒体库模式下必须拿到 TMDB 匹配（用于番剧/电视剧/纪录片分类）
        need_match = bool({"title_orig", "title_user", "year"} & needed) \
            or options.output_mode == "library"
        match = forced_match
        if not match and need_match:
            if not info.title:
                return self._to_manual(item, "cannot parse title from filename")
            matches = self.tmdb.search(info.title, year=info.year,
                                       media_type="tv",
                                       language=tmdb_lang(options.language))
            logger.info("TMDB 搜索 %s (year=%s): %d 个候选",
                        info.title, info.year, len(matches))
            match = self._pick_match(matches, info.year)
            if match:
                logger.info("TMDB 选中: %s (%s) id=%s genres=%s",
                            match.title_orig, match.title_user, match.tmdb_id, match.genres)
            if not match:
                return self._to_manual(item, "no TMDB TV match")
        item.match = match

        # 媒体库模式：分类剧集类型，识别失败进人工队列
        if options.output_mode == "library":
            tv_type = classify_tv_type(match.genres if match else [])
            logger.info("剧集类型分类: %s", tv_type)
            if not tv_type:
                return self._to_manual(
                    item, "cannot determine TV type (anime/drama/documentary)")
            info.extra["tv_type"] = tv_type

        values = self._base_values(info, group, match)
        if match and info.season is not None and info.episode is not None:
            values["season"] = info.season
            values["episode"] = info.episode
            # 集标题（本地化）按 UI 语言优先级解析（媒体库模式必须取到）
            if "title_user" in needed or is_library:
                ep_user = self._resolve_title_user(
                    match, "tv", options.language,
                    episode_key=(info.season, info.episode))
                if ep_user:
                    values["title_user"] = ep_user
        return self._finalize(item, options, values)

    # ── 音乐 pipeline ─────────────────────────────────────────────────────

    def _process_music(self, item, options) -> QueueItem:
        needed = required_fields(options.format)
        is_library = options.output_mode == "library"
        # 酷狗加密音频：先解密（输出回原目录，成功后删加密源），再读标签
        if os.path.splitext(item.path)[1].lower() in KUGOU_EXTS:
            try:
                decrypted = decrypt_kugou(
                    item.path, self.config.get("music.kugou_db", ""))
            except UnlockError as e:
                logger.error("酷狗解密失败 %s: %s", item.path, e)
                return self._to_manual(item, str(e))
            if not decrypted:
                return self._to_manual(item, "酷狗解密未生成输出文件")
            item.source_path = item.path  # 记录原始加密路径，供 UI 定位行
            item.path = decrypted[0]
        tags = read_music_tags(item.path)
        title = (tags.get("title") or [""])[0].strip()
        artists = split_artists(tags.get("artist"),
                                self.config.get("music.artist_separators", ""))
        logger.info("音乐标签: title=%s artists=%s album=%s",
                    title, artists, tags.get("album"))
        if not title or not artists:
            return self._to_manual(item, "missing title/artist in metadata tags")

        values: Dict[str, object] = {}
        if "title" in needed or is_library:
            values["title"] = title
        if "artist" in needed or is_library:
            values["artist"] = self._artist_join_sep().join(artists)
        if is_library and artists:
            values["artist_first"] = artists[0]  # 媒体库目录用第一位艺术家
        if ("album" in needed or is_library) and tags.get("album"):
            # 媒体库模式目录结构 Music/Artist/Album 恒需专辑，不依赖格式表达式
            values["album"] = tags["album"][0]
        if "year" in needed and tags.get("date"):
            values["year"] = str(tags["date"][0])[:4]
        if "track" in needed and tags.get("tracknumber"):
            values["track"] = tags["tracknumber"][0]

        # 文件操作后在目标上写回拆分后的多艺术家 + 可选封面（不污染源文件）
        post = lambda dst: self._apply_music_post(dst, artists, options)
        return self._finalize(item, options, values, post=post)

    def _artist_join_sep(self) -> str:
        """多艺术家拼接分隔符：配置含空格（如 ", "）整体作为连接符；
        纯字符集合（如 "、,，"）取第一个字符；默认英文逗号加空格 ", "。"""
        sep = self.config.get("music.artist_separators", "") or ", "
        if not sep:
            return ", "
        if any(c.isspace() for c in sep):
            return sep
        return sep[0]

    # ── 字幕跟随视频 ─────────────────────────────────────────────────────

    def _process_subtitle(self, item, options) -> QueueItem:
        """字幕文件：按 (季, 集) 配对到同集视频，改名/移动跟随视频。

        目标名 = 视频目标名（去掉扩展名）+ 语言标签 + 字幕扩展名，例如
        ``... S01E10 xxx - [DBD 1920x1080].简中.ass``。
        """
        info = extract_from_filename(item.path)
        item.info = info
        logger.info("字幕解析: title=%s season=%s episode=%s",
                    info.title, info.season, info.episode)
        if info.season is None or info.episode is None:
            return self._to_manual(item, "cannot parse season/episode from subtitle")
        video = self._video_by_ep.get((info.season, info.episode))
        if video is None or not video.dst:
            return self._to_manual(item, "no matching video for subtitle")
        ext = os.path.splitext(item.path)[1]
        tag = self._subtitle_lang_tag(item.path, video.path)
        base = os.path.splitext(video.dst)[0]
        dst = f"{base}.{tag}{ext}"
        item.dst = dst
        item.new_name = os.path.basename(dst)
        logger.info("字幕跟随视频: %s -> %s", os.path.basename(item.path),
                    os.path.basename(dst))
        if options.dry_run:
            item.status = "ok"
            return item
        result = self._apply_op(item.path, dst, options.mode)
        if not result.ok:
            item.status = "error"
            item.error = result.error or "file operation failed"
            logger.error("字幕操作失败 %s -> %s: %s", item.path, dst, item.error)
            return item
        item.status = "ok"
        return item

    @staticmethod
    def _subtitle_lang_tag(sub_path: str, video_path: str) -> str:
        """从字幕文件名提取语言标签（相对视频名的尾部增量）并规范化。

        如 ``xxx.scjp.ass`` 相对视频 ``xxx.mkv`` 提取 ``scjp`` → 规范化为 ``简中``；
        无差异（同名字幕）时返回 ``sub``。
        """
        sub_stem = os.path.splitext(os.path.basename(sub_path))[0]
        video_stem = os.path.splitext(os.path.basename(video_path))[0]
        n = 0
        while n < min(len(sub_stem), len(video_stem)) and sub_stem[n] == video_stem[n]:
            n += 1
        tag = sub_stem[n:].lstrip(". -_")
        if not tag:
            return "sub"
        return _SUBTITLE_LANG_NORM.get(tag.lower(), tag)

    # ── 特典整理（extras） ───────────────────────────────────────────────

    def _tmdb_tv_match(self, info: MediaInfo, options) -> Optional[MediaMatch]:
        """按解析标题搜 TMDB 剧集并选中（复用 tv pipeline 的匹配逻辑）。"""
        if not info or not info.title:
            return None
        matches = self.tmdb.search(info.title, year=info.year,
                                   media_type="tv",
                                   language=tmdb_lang(options.language))
        if matches:
            logger.info("TMDB 搜索(特典) %s: %d 个候选", info.title, len(matches))
            return self._pick_match(matches, info.year)
        logger.info("TMDB 搜索(特典) %s: 0 个候选", info.title)
        return None

    def _movie_extras_match(self, item, options) -> Optional[MediaMatch]:
        """电影特典：主片信息从路径上级文件夹解析（文件名通常不含片名）。

        从文件所在目录逐级向上，取第一个能解析出标题的目录作为主片文件夹
        （特典目录如 Featurettes 解析不出标题，自然跳过）。"""
        d = os.path.dirname(os.path.abspath(item.path))
        info = None
        while True:
            parent = os.path.basename(d).strip()
            if parent:
                info = extract_from_filename(parent)
                if info and info.title:
                    break
            up = os.path.dirname(d)
            if up == d:
                info = None
                break
            d = up
        if not info or not info.title:
            logger.warning("电影特典找不到主片文件夹: %s", item.path)
            return None
        logger.info("电影特典主片(文件夹): %s (%s)", info.title, info.year)
        matches = self.tmdb.search(info.title, year=info.year,
                                   media_type="movie",
                                   language=tmdb_lang(options.language))
        if matches:
            logger.info("TMDB 搜索(电影特典) %s: %d 个候选", info.title, len(matches))
            return self._pick_match(matches, info.year)
        return None

    def _extras_parts(self, info: MediaInfo) -> tuple:
        """特典命名三要素 ``(frag, seq, name)``：类型词 / 原序号(或 None) / 名字(或 None)。

        用户规范：``Show - PV - 特报映像.mkv`` → ``PV 01 特报映像.mkv``
        （类型词 + 序号 + 名字，均以空格分隔）。用类型词作锚点：它之前是
        作品名（父目录已有剧名，丢弃）；序号取它之后紧邻的第一个数字（可能在
        方括号内，如 ``[NCED10]`` → 10）；名字只取方括号外的描述文本。若类型词
        只是较长名字的子串（如 ``特典映像`` 里的 ``特典``）则合并为整体片段。
        """
        base = os.path.splitext(os.path.basename(info.filename or ""))[0]
        frag = _norm_extras_fragment(
            str((info.extra or {}).get("extras_frag") or ""))
        if not frag:
            frag = "Extra"
        m = re.search(re.escape(frag), base, re.I)
        if not m:
            return frag, None, None
        rest = base[m.end():]
        # 类型词是较长名字的子串：紧贴且非数字/括号/分隔符开头（如 特典映像）→ 合并整体
        if rest and re.match(r"^[^\s\-–\[()\]\d]", rest):
            whole = _norm_extras_fragment(base[m.start():])
            return whole, None, None
        # 光盘盘号：``[D1]`` / ``[D2]`` → 前置 ``Disc N``，如 ``Disc 1 Menu 01.mkv``
        m_disc = re.search(r"\[[Dd]\d+\]", rest)
        if m_disc:
            disc_no = re.search(r"\d+", m_disc.group()).group()
            frag = f"Disc {disc_no} {frag}"
            rest = rest.replace(m_disc.group(), " ")
        # 序号：类型词后紧邻的第一个数字（可能在方括号内）
        seq_rest = re.sub(r"^[\s\-–\[\]\(\)]+", "", rest)
        seq_rest = _EXTRAS_DETAIL_RE.sub(" ", seq_rest)
        m_seq = re.match(r"(\d{1,4})", seq_rest)
        seq = int(m_seq.group(1)) if m_seq else None
        # 名字：仅方括号外文本（剥详情/分隔/前导序号）
        outer = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", rest)
        outer = _EXTRAS_DETAIL_RE.sub(" ", outer)
        outer = re.sub(r"^[\s\-–\d]+", "", outer)
        outer = re.sub(r"[\[\](){}_.\-]", " ", outer)
        outer = re.sub(r"\s+", " ", outer).strip(" -")
        return frag, seq, (outer or None)

    def _next_extras_number(self, folder: str, fragment: str) -> int:
        """在目标 extras 目录中为同一“视频名片段”计算下一个序号（01、02…）。

        优先取目录中已有同名文件的末尾序号，其次本批已分配序号（dry-run
        阶段文件尚未落盘，需内存补账）。
        """
        prefix = re.escape(fragment)
        pat = re.compile(rf"^{prefix}\s*(\d+)", re.I)
        max_n = 0
        try:
            for name in os.listdir(folder):
                m = pat.match(name)
                if m:
                    max_n = max(max_n, int(m.group(1)))
        except OSError:
            pass
        key = (os.path.normcase(folder), fragment.lower())
        max_n = max(max_n, self._extras_seq.get(key, 0))
        n = max_n + 1
        self._extras_seq[key] = n
        return n

    def _process_extras(self, item, options) -> QueueItem:
        """特典文件（NCOP/NCED/PV/menu/Tokuten 等）：整理到媒体库 extras 子目录。

        - 不当作正片集：不参与季/集解析、不进字幕配对缓存。
        - library 模式落到 ``<root>/<Show> (year)/<extras>/``（按剧集类型选根），
          文件名按用户规范重命名：类型词 + 序号 + 名字（如有），如
          ``PV 01 特报映像.mkv`` / ``NCOP 01.mkv``（无名字退化为类型+序号）。
        - custom 模式落到输出目录（或源目录），文件名保留。
        """
        info = item.info
        extras_type = str((info.extra or {}).get("extras") or "Other")
        logger.info("特典识别: %s (%s) kind=%s", info.title, extras_type, item.kind)
        # 主片匹配：TV 特典用文件名标题搜剧集；电影特典从上级文件夹解析主片后搜电影
        if item.kind == "movie":
            match = self._movie_extras_match(item, options)
        else:
            match = self._tmdb_tv_match(info, options)
        if match is None:
            return self._to_manual(item, "no TMDB match for extras")
        item.match = match

        dst = None
        if options.output_mode == "library" and options.library_roots:
            title = sanitize_filename(match.title_orig)
            year = match.year
            folder = f"{title} ({year})" if year else title
            if item.kind == "movie":
                # 电影特典：落到 Movies/<Movie> (year)/<extras>/，保留原文件名（即特典描述）
                root = options.library_roots.get("movie")
                if root:
                    folder_path = os.path.join(root, folder, extras_type)
                    dst = os.path.join(folder_path, os.path.basename(item.path))
            else:
                tv_type = classify_tv_type(match.genres)
                root = options.library_roots.get(f"tv_{tv_type}") if tv_type else None
                if root:
                    folder_path = os.path.join(root, folder, extras_type)
                    ext = os.path.splitext(item.path)[1]
                    frag, seq, name = self._extras_parts(info)
                    n = seq if seq else self._next_extras_number(folder_path, frag)
                    new_name = f"{frag} {n:02d}"
                    if name:
                        new_name += f" {name}"
                    new_name += ext
                    dst = os.path.join(folder_path, new_name)
        if dst is None:  # custom 模式或无对应根目录：落到输出目录/源目录，保留原文件名
            base = options.output_dir or os.path.dirname(os.path.abspath(item.path))
            dst = os.path.join(base, os.path.basename(item.path))
        item.dst = dst
        item.new_name = os.path.basename(dst)
        logger.info("特典整理: %s -> %s", os.path.basename(item.path), dst)
        if options.dry_run:
            item.status = "ok"
            return item
        result = self._apply_op(item.path, dst, options.mode)
        if not result.ok:
            item.status = "error"
            item.error = result.error or "file operation failed"
            logger.error("特典操作失败 %s -> %s: %s", item.path, dst, item.error)
            return item
        item.status = "ok"
        return item

    def _apply_music_post(self, dst: str, artists: List[str], options: ProcessingOptions) -> None:
        if not update_music_tags(dst, {"artist": artists}):
            return
        if options.inject_cover and options.cover_path and os.path.isfile(options.cover_path):
            if options.cover_path.lower().endswith((".jpg", ".jpeg", ".png")):
                inject_cover(dst, options.cover_path)
            else:  # 从另一音乐文件复制封面
                copy_cover(options.cover_path, dst)

    # ── 公共辅助 ──────────────────────────────────────────────────────────

    def resolve_subgroups(self, raw: str, group_from_filename: Optional[str]) -> List[str]:
        """识别文件名中的多个组（如 ``Tigole-QxR`` / ``SweetSub&VCB-Studio``），
        返回规范名列表。回退链：本地库 → LLM → []（进手动队列）。

        - 先按显式连接符（``&``/``×``/``/``）拆，再对每个子段尝试按 ``-``
          拆出已知组；整段可识别（如 ``VCB-Studio``）则保留整段。
        - 只保留能识别为已知组的片段；一个都识别不到且无 LLM 时返回空列表。
        """
        candidates = self._split_group_candidates(group_from_filename)
        if not candidates:
            candidates = self._split_group_candidates(raw)
        groups: List[str] = []
        for cand in candidates:
            key = self.subgroups.recognize(cand)
            if key:
                name = self.subgroups.display_name(key)
                if name not in groups:
                    groups.append(name)
        if groups:
            logger.info("字幕组识别(本地库): %r -> %s", group_from_filename, groups)
            return groups
        if self.llm.available:
            result = self.llm.parse_subgroup(raw)
            if result:
                self.subgroups.add(result["subgroup"], result["aliases"])
                logger.info("字幕组识别(LLM): %s", result["subgroup"])
                return [result["subgroup"]]
        logger.warning("字幕组识别失败: %r", raw)
        return []

    def _split_group_candidates(self, frag: Optional[str]) -> List[str]:
        """把组名片段拆成候选子片段（多组场景）。

        先按 ``&``/``×``/``/`` 拆出显式多组；每个子段若整段精确等于已知组
        （如 ``VCB-Studio``）保留整段，否则尝试按 ``-`` 拆出已知组（如
        ``Tigole-QxR`` → ``Tigole`` + ``QxR``）。用整段精确匹配判断，避免
        ``Tigole-QxR`` 被 ``Tigole``/``QxR`` 子串误判为单个已知组。
        """
        if not frag:
            return []
        parts = [p.strip() for p in re.split(r"[&×／/]", str(frag)) if p.strip()]
        result: List[str] = []
        for p in parts:
            if self._is_known_group_exact(p):
                result.append(p)
                continue
            sub = [s.strip() for s in re.split(r"-", p) if s.strip()]
            if len(sub) > 1:
                known = [s for s in sub if self._is_known_group_exact(s)]
                if known:
                    result.extend(known)
                    continue
            result.append(p)
        return result

    def _is_known_group_exact(self, frag: str) -> bool:
        """整段（去标点后）是否精确等于某个已知组的 key/简称/别名。"""
        n = _norm_group_name(frag)
        if not n:
            return False
        for key, meta in self.subgroups.data["groups"].items():
            rename_to = meta.get("rename_to") or key
            for cand in (key, rename_to, *meta.get("aliases", [])):
                if _norm_group_name(cand) == n:
                    return True
        return False

    def _base_values(self, info: MediaInfo, group: Optional[str],
                     match: Optional[MediaMatch]) -> Dict[str, object]:
        values: Dict[str, object] = {}
        if match:
            values["title_orig"] = match.title_orig
            values["title_user"] = match.title_user
            if match.year:
                values["year"] = match.year
        elif info.title:
            values["title_orig"] = info.title
        if info.year and "year" not in values:
            values["year"] = info.year
        if group:
            values["group"] = group
        if info.resolution:
            values["resolution"] = info.resolution
        if info.quality:
            values["quality"] = info.quality
        return values

    @staticmethod
    def _pick_match(matches: List[MediaMatch], year: Optional[int]) -> Optional[MediaMatch]:
        if not matches:
            return None
        if year:
            for m in matches:
                if m.year == year:
                    return m
        return matches[0]

    def _resolve_title_user(self, match: MediaMatch, media_type: str,
                            ui_lang: str,
                            episode_key: Optional[tuple] = None) -> str:
        """按 UI 语言优先级链解析 ``title_user``（opencc 简繁转换）。

        - 中文 UI：先查对应简体/繁体，缺翻译时查另一种体并用 opencc 转换，
          再回退英语，最后回退影片原始标题。
        - 英文 UI：先英语，最后原始标题。
        ``episode_key=(season, episode)`` 时取集标题，否则取电影/剧集标题。
        """
        engine = self.config.get("localize.engine", "opencc") or "opencc"
        for tmdb_code, mode in title_language_chain(ui_lang):
            if episode_key is not None:
                m = self.tmdb.get_episode(match.tmdb_id, episode_key[0],
                                          episode_key[1], tmdb_code)
                t = m.episode_title_user if m else None
            else:
                t = self.tmdb.get_title(match.tmdb_id, media_type, tmdb_code)
            if looks_localized(t, match.title_orig):
                return convert_title(t, mode, engine)
        return match.title_orig

    def _finalize(self, item: QueueItem, options: ProcessingOptions,
                  values: Dict[str, object],
                  post: Optional[Callable[[str], None]] = None) -> QueueItem:
        ext = os.path.splitext(item.path)[1]
        if options.output_mode == "library":
            # 媒体库模式：文件名用库模板（Jellyfin 官方格式 + 更多影片信息），
            # 目录保持 Jellyfin 结构；表格显示相对路径（含目录层级）
            rel = self._library_rel_path(item, values, ext)
            item.new_name = rel
            dst = self._build_dst(item, options, values, ext)
        else:
            new_base = evaluate(options.format, values)
            if not new_base:
                return self._to_manual(item, "format produced empty name")
            item.new_name = sanitize_filename(new_base) + ext
            dst = self._build_dst(item, options, values, ext)
        item.dst = dst
        logger.info("格式化: %s -> %s", os.path.basename(item.path), item.new_name)
        if options.dry_run:
            # 匹配阶段：只计算目标名/路径，不执行文件操作（等用户点“执行”）
            item.status = "ok"
            return item
        result = self._apply_op(item.path, dst, options.mode)
        if not result.ok:
            item.status = "error"
            item.error = result.error or "file operation failed"
            logger.error("文件操作失败 %s -> %s: %s", item.path, dst, item.error)
            return item
        logger.info("文件操作 %s 成功: %s -> %s", options.mode, item.path, dst)
        if post:
            try:
                post(dst)
            except Exception as e:
                item.status = "error"
                item.error = f"post-processing failed: {e}"
                return item
        item.status = "ok"
        return item

    # ── 目标路径构建（自定义 / 媒体库 Jellyfin 结构） ──────────────────────

    def _build_dst(self, item: QueueItem, options: ProcessingOptions,
                   values: Dict[str, object], ext: str) -> str:
        """构建目标文件完整路径。

        - ``library``：按 Jellyfin 规范（含 extras 子目录）落到对应根目录下。
        - ``custom``：落到自定义输出目录（或源目录）。
        """
        if options.output_mode == "library" and options.library_roots:
            root_key = item.kind
            if item.kind == "tv":
                # 剧集按识别出的类型选择根目录（番剧/电视剧/纪录片）
                tv_type = (item.info.extra or {}).get("tv_type") or "drama"
                root_key = f"tv_{tv_type}"
            root = options.library_roots.get(root_key)
            if root:
                rel = self._library_rel_path(item, values, ext)
                return os.path.join(root, rel)
        dst_dir = os.path.abspath(options.output_dir) if options.output_dir \
            else os.path.dirname(os.path.abspath(item.path))
        return os.path.join(dst_dir, item.new_name)

    # 媒体库模式文件名模板（Jellyfin 官方格式 + 更多影片信息）
    _LIBRARY_FORMATS = {
        "movie": "{title_orig} ({year}) -[{group} {resolution}]",
        "tv": "{title_orig} S{season_2d}E{episode_2d} {title_user} - [{group} {resolution}]",
        "music": "{artist} - {title}",
    }

    def _library_filename(self, item: QueueItem, values: Dict[str, object],
                          ext: str) -> str:
        """媒体库模式的文件名：按库模板生成并清洗。"""
        fmt = self._LIBRARY_FORMATS.get(item.kind, "{title_orig}")
        base = evaluate(fmt, values)
        base = sanitize_filename(base) or "Unknown"
        return base + ext

    def _library_rel_path(self, item: QueueItem, values: Dict[str, object],
                          ext: str) -> str:
        """按 Jellyfin 规范构建媒体库内的相对路径 + 文件名。

        - 电影: ``Movies/T (2009)/<库模板文件名>``
        - 剧集: ``TV/S (2009)/Season 01/<库模板文件名>``
        - 音乐: ``Music/Artist/Album/<库模板文件名>``
        文件名由 :meth:`_library_filename` 按库模板生成（含更多影片信息）。
        """
        info = item.info
        extras = (info.extra or {}).get("extras") if info else None
        title = str(values.get("title_orig") or (info.title if info else "") or "Unknown")
        title = sanitize_filename(title)
        year = values.get("year") or (info.year if info else None)
        folder = f"{title} ({year})" if year else title

        name = self._library_filename(item, values, ext)
        if item.kind == "tv":
            season = int(values.get("season") or (info.season if info else 1))
            folder = os.path.join(folder, f"Season {season:02d}")
        elif item.kind == "music":
            artist = sanitize_filename(str(
                values.get("artist_first") or values.get("artist") or "Unknown Artist"))
            album = sanitize_filename(str(values.get("album") or "Unknown Album"))
            folder = os.path.join(artist, album)

        if extras:
            folder = os.path.join(folder, extras)
        return os.path.join(folder, name)

    def _apply_op(self, src: str, dst: str, mode: str) -> OperationResult:
        if mode == "rename":
            return self.operator.rename(src, dst)
        if mode == "copy":
            return self.operator.copy(src, dst)
        if mode == "hardlink":
            return self.operator.hardlink(src, dst)
        raise ValueError(f"unknown mode: {mode}")

    def execute_item(self, item: QueueItem, options: ProcessingOptions) -> QueueItem:
        """对已匹配（dry_run 完成）的项执行真实文件操作。

        - 电影/剧集：直接 rename / copy / hardlink 到 :attr:`QueueItem.dst`。
        - 音乐：操作后还在目标上写回拆分后的艺术家/封面（与直接处理一致）。
        """
        if item.status != "ok" or not item.dst:
            return item  # 未匹配或已执行
        result = self._apply_op(item.path, item.dst, options.mode)
        if not result.ok:
            item.status = "error"
            item.error = result.error or "file operation failed"
            logger.error("文件操作失败 %s -> %s: %s",
                         item.path, item.dst, item.error)
            return item
        logger.info("文件操作 %s 成功: %s -> %s", options.mode, item.path, item.dst)
        if item.kind == "music":
            try:
                tags = read_music_tags(item.path)
                artists = split_artists(
                    tags.get("artist"),
                    self.config.get("music.artist_separators", ""))
                self._apply_music_post(item.dst, artists, options)
            except Exception as e:
                item.status = "error"
                item.error = f"post-processing failed: {e}"
                return item
        item.status = "ok"
        return item

    def _to_manual(self, item: QueueItem, reason: str) -> QueueItem:
        item.status = "manual"
        item.reason = reason
        self.manual_queue.append(item)
        self._persist_manual()
        logger.warning("进入人工队列: %s (原因=%s)", item.path, reason)
        return item

    def _persist_manual(self) -> None:
        """把当前手动队列写盘。"""
        if self.manual_store is not None:
            self.manual_store.save_items([i.as_dict() for i in self.manual_queue])

    def load_manual_items(self) -> List[QueueItem]:
        """从磁盘恢复手动队列（应用重启后可继续处理）。"""
        for d in self.manual_store.load_items():
            self.manual_queue.append(
                QueueItem(
                    path=d.get("path", ""),
                    kind=d.get("kind", "movie"),
                    status="manual",
                    format=d.get("format", ""),
                    new_name=d.get("new_name", ""),
                    reason=d.get("reason", ""),
                )
            )
        return self.manual_queue
