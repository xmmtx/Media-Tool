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

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

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


@dataclass
class ProcessingOptions:
    """单文件处理参数。"""

    kind: str                    # movie | tv | music
    format: str                  # 表达式（如 "{title_orig} ({year}) - [{group}]"）
    mode: str = "rename"         # rename | copy | hardlink
    output_dir: Optional[str] = None  # 目标目录（None = 源文件所在目录）
    language: str = "zh-CN"
    inject_cover: bool = False   # 音乐：是否注入封面
    cover_path: Optional[str] = None  # 封面图片路径，或源音乐文件路径（自动识别）


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
    ) -> QueueItem:
        """处理单个文件；``forced_match`` / ``forced_group`` 供手动干预后重跑。"""
        item = QueueItem(path=path, kind=options.kind)
        item.format = options.format
        try:
            if options.kind == "movie":
                return self._process_movie(item, options, forced_match, forced_group)
            if options.kind == "tv":
                return self._process_tv(item, options, forced_match, forced_group)
            if options.kind == "music":
                return self._process_music(item, options)
        except Exception as e:  # 运行期错误不中断整个批次
            item.status = "error"
            item.error = str(e)
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

        group = forced_group
        if "group" in needed and not group:
            group = self.resolve_subgroup(item.path, info.group)
            item.group = group
            if not group:
                return self._to_manual(item, "subgroup not recognized")

        if "resolution" in needed:
            info.resolution = probe_resolution(item.path)

        match = forced_match
        if not match and {"title_orig", "title_user", "year"} & needed:
            if not info.title:
                return self._to_manual(item, "cannot parse title from filename")
            matches = self.tmdb.search(info.title, year=info.year,
                                       media_type="movie", language=options.language)
            match = self._pick_match(matches, info.year)
            if not match:
                return self._to_manual(item, "no TMDB movie match")
        item.match = match

        values = self._base_values(info, group, match)
        return self._finalize(item, options, values)

    # ── 剧集 pipeline ─────────────────────────────────────────────────────

    def _process_tv(self, item, options, forced_match=None, forced_group=None) -> QueueItem:
        needed = required_fields(options.format)
        info = extract_from_filename(item.path)
        item.info = info

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

        match = forced_match
        if not match and {"title_orig", "title_user", "year"} & needed:
            if not info.title:
                return self._to_manual(item, "cannot parse title from filename")
            matches = self.tmdb.search(info.title, year=info.year,
                                       media_type="tv", language=options.language)
            match = self._pick_match(matches, info.year)
            if not match:
                return self._to_manual(item, "no TMDB TV match")
        item.match = match

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
        artists = split_artists(tags.get("artist"))
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
            return self.subgroups.display_name(key)
        if self.llm.available:
            result = self.llm.parse_subgroup(raw)
            if result:
                self.subgroups.add(result["subgroup"], result["aliases"])
                return result["subgroup"]
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
        dst_dir = os.path.abspath(options.output_dir) if options.output_dir \
            else os.path.dirname(os.path.abspath(item.path))
        dst = os.path.join(dst_dir, item.new_name)
        result = self._apply_op(item.path, dst, options.mode)
        if not result.ok:
            item.status = "error"
            item.error = result.error or "file operation failed"
            return item
        if post:
            try:
                post(dst)
            except Exception as e:
                item.status = "error"
                item.error = f"post-processing failed: {e}"
                return item
        item.status = "ok"
        return item

    def _apply_op(self, src: str, dst: str, mode: str) -> OperationResult:
        if mode == "rename":
            return self.operator.rename(src, dst)
        if mode == "copy":
            return self.operator.copy(src, dst)
        if mode == "hardlink":
            return self.operator.hardlink(src, dst)
        raise ValueError(f"unknown mode: {mode}")

    def _to_manual(self, item: QueueItem, reason: str) -> QueueItem:
        item.status = "manual"
        item.reason = reason
        self.manual_queue.append(item)
        self._persist_manual()
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
