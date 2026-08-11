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

try:
    from ..db import ConfigStore, ManualQueueStore, SubgroupStore  # src 作为顶层包运行时
except ImportError:  # src 作为 sys.path 根运行时
    from db import ConfigStore, ManualQueueStore, SubgroupStore  # type: ignore
from .extractors.media_extractor import (
    MediaInfo,
    extract_from_filename,
    probe_resolution,
)
from .file_ops import FileOperator, OperationResult
from .formatters.expression_engine import evaluate, required_fields
from .metadata.music_tags import (
    copy_cover,
    inject_cover,
    read_music_tags,
    split_artists,
    update_music_tags,
)
from .providers import LlmSubgroupProvider, MediaMatch, TMDBProvider

# Windows 非法文件名字符（含控制符）
_INVALID_WIN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


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
            if options.kind == "movie":
                return self._process_movie(item, options, forced_match, forced_group)
            if options.kind == "tv":
                return self._process_tv(item, options, forced_match, forced_group,
                                        forced_values)
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
        info = extract_from_filename(item.path)
        item.info = info
        logger.info("文件名解析: title=%s year=%s", info.title, info.year)

        group = forced_group
        if "group" in needed and not group:
            group = self.resolve_subgroup(item.path, info.group)
            item.group = group
            if not group:
                return self._to_manual(item, "subgroup not recognized")

        if "resolution" in needed:
            info.resolution = probe_resolution(item.path)
            logger.info("分辨率(ffprobe): %s", info.resolution)

        match = forced_match
        if not match and {"title_orig", "title_user", "year"} & needed:
            if not info.title:
                return self._to_manual(item, "cannot parse title from filename")
            matches = self.tmdb.search(info.title, year=info.year,
                                       media_type="movie", language=options.language)
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
        return self._finalize(item, options, values)

    # ── 剧集 pipeline ─────────────────────────────────────────────────────

    def _process_tv(self, item, options, forced_match=None, forced_group=None,
                    forced_values: Optional[Dict[str, object]] = None) -> QueueItem:
        needed = required_fields(options.format)
        info = extract_from_filename(item.path)
        item.info = info
        logger.info("文件名解析: title=%s year=%s season=%s episode=%s",
                    info.title, info.year, info.season, info.episode)

        # 手动匹配：直接用预填字段（集标题/季/集）走格式化，跳过 TMDB 搜索与分类
        if forced_values is not None:
            values = dict(forced_values)
            logger.info("手动匹配预填值: %s", values)
            if "group" in needed:
                group = forced_group or self.resolve_subgroup(item.path, info.group)
                item.group = group
                if group:
                    values["group"] = group
            if "resolution" in needed:
                info.resolution = probe_resolution(item.path)
                if info.resolution:
                    values["resolution"] = info.resolution
            if info.year and "year" not in values:
                values["year"] = info.year
            return self._finalize(item, options, values)

        if {"season", "episode"} & needed and (info.season is None or info.episode is None):
            return self._to_manual(item, "cannot parse season/episode")

        group = forced_group
        if "group" in needed and not group:
            group = self.resolve_subgroup(item.path, info.group)
            item.group = group
            if not group:
                return self._to_manual(item, "subgroup not recognized")

        if "resolution" in needed:
            info.resolution = probe_resolution(item.path)

        # 媒体库模式下必须拿到 TMDB 匹配（用于番剧/电视剧/纪录片分类）
        need_match = bool({"title_orig", "title_user", "year"} & needed) \
            or options.output_mode == "library"
        match = forced_match
        if not match and need_match:
            if not info.title:
                return self._to_manual(item, "cannot parse title from filename")
            matches = self.tmdb.search(info.title, year=info.year,
                                       media_type="tv", language=options.language)
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
            # 剧集标题（本地化）优先作为 title_user
            if "title_user" in needed:
                ep = self.tmdb.get_episode(match.tmdb_id, info.season, info.episode,
                                           options.language)
                if ep and ep.episode_title_user:
                    values["title_user"] = ep.episode_title_user
        return self._finalize(item, options, values)

    # ── 音乐 pipeline ─────────────────────────────────────────────────────

    def _process_music(self, item, options) -> QueueItem:
        needed = required_fields(options.format)
        tags = read_music_tags(item.path)
        title = (tags.get("title") or [""])[0].strip()
        artists = split_artists(tags.get("artist"),
                                self.config.get("music.artist_separators", ""))
        logger.info("音乐标签: title=%s artists=%s album=%s",
                    title, artists, tags.get("album"))
        if not title or not artists:
            return self._to_manual(item, "missing title/artist in metadata tags")

        values: Dict[str, object] = {}
        if "title" in needed:
            values["title"] = title
        if "artist" in needed:
            values["artist"] = "、".join(artists)
        if "album" in needed and tags.get("album"):
            values["album"] = tags["album"][0]
        if "year" in needed and tags.get("date"):
            values["year"] = str(tags["date"][0])[:4]
        if "track" in needed and tags.get("tracknumber"):
            values["track"] = tags["tracknumber"][0]

        # 文件操作后在目标上写回拆分后的多艺术家 + 可选封面（不污染源文件）
        post = lambda dst: self._apply_music_post(dst, artists, options)
        return self._finalize(item, options, values, post=post)

    def _apply_music_post(self, dst: str, artists: List[str], options: ProcessingOptions) -> None:
        if not update_music_tags(dst, {"artist": artists}):
            return
        if options.inject_cover and options.cover_path and os.path.isfile(options.cover_path):
            if options.cover_path.lower().endswith((".jpg", ".jpeg", ".png")):
                inject_cover(dst, options.cover_path)
            else:  # 从另一音乐文件复制封面
                copy_cover(options.cover_path, dst)

    # ── 公共辅助 ──────────────────────────────────────────────────────────

    def resolve_subgroup(self, raw: str, group_from_filename: Optional[str]) -> Optional[str]:
        """字幕组识别回退链：本地库 → LLM → None（进手动队列）。

        返回规范名（``rename_to``）；识别不到且无 LLM 时返回 ``None``。
        """
        key = self.subgroups.recognize(group_from_filename) or self.subgroups.recognize(raw)
        if key:
            name = self.subgroups.display_name(key)
            logger.info("字幕组识别(本地库): %r -> %s", group_from_filename, name)
            return name
        if self.llm.available:
            result = self.llm.parse_subgroup(raw)
            if result:
                self.subgroups.add(result["subgroup"], result["aliases"])
                logger.info("字幕组识别(LLM): %s", result["subgroup"])
                return result["subgroup"]
        logger.warning("字幕组识别失败: %r", raw)
        return None

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

    def _finalize(self, item: QueueItem, options: ProcessingOptions,
                  values: Dict[str, object],
                  post: Optional[Callable[[str], None]] = None) -> QueueItem:
        ext = os.path.splitext(item.path)[1]
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

    def _library_rel_path(self, item: QueueItem, values: Dict[str, object],
                          ext: str) -> str:
        """按 Jellyfin 规范构建媒体库内的相对路径 + 文件名。

        - 电影: ``Movies/T (2009)/T (2009).ext``（extras 进 ``/ExtrasType/``）
        - 剧集: ``TV/S (2009)/Season 01/S S01E01.ext``
        - 音乐: ``Music/Artist/Album/NN - Title.ext``
        """
        info = item.info
        extras = (info.extra or {}).get("extras") if info else None
        title = str(values.get("title_orig") or (info.title if info else "") or "Unknown")
        title = sanitize_filename(title)
        year = values.get("year") or (info.year if info else None)
        folder = f"{title} ({year})" if year else title

        if item.kind == "movie":
            name = f"{folder}{ext}"
        elif item.kind == "tv":
            season = int(values.get("season") or (info.season if info else 1))
            episode = int(values.get("episode") or (info.episode if info else 1))
            folder = os.path.join(folder, f"Season {season:02d}")
            name = f"{title} S{season:02d}E{episode:02d}{ext}"
        else:  # music
            artist = sanitize_filename(str(values.get("artist") or "Unknown Artist"))
            album = sanitize_filename(str(values.get("album") or "Unknown Album"))
            folder = os.path.join(artist, album)
            prefix = ""
            track = values.get("track")
            if track:
                try:
                    prefix = f"{int(str(track).split('/')[0]):02d} - "
                except (TypeError, ValueError):
                    prefix = ""
            name = f"{prefix}{sanitize_filename(str(values.get('title') or 'Unknown'))}{ext}"

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
