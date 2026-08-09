"""src.core.metadata — 音乐标签读写与艺术家拆分。"""

from .music_tags import (
    HAS_MUTAGEN,
    available,
    copy_cover,
    inject_cover,
    read_cover,
    read_music_tags,
    split_artists,
    update_music_tags,
)

__all__ = [
    "HAS_MUTAGEN",
    "available",
    "split_artists",
    "read_music_tags",
    "update_music_tags",
    "read_cover",
    "inject_cover",
    "copy_cover",
]
