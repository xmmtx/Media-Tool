"""音乐标签读写（mutagen，可选依赖）。

- **艺术家拆分**: :func:`split_artists` 把 ``歌手A、歌手B / C, D`` 拆分为
  独立艺术家列表，供 ID3 ``ARTIST``/``ARTISTS`` 多值标签使用。
- **标签读写**: :func:`read_music_tags` / :func:`update_music_tags` 统一
  处理 MP3(ID3) / FLAC(VorbisComment) 等格式，多艺术家以列表形式存取。
- **封面管理**: :func:`inject_cover`（从图片文件）、:func:`copy_cover`
  （从另一音乐文件复制）、:func:`read_cover`（读取已有封面）。

mutagen 未安装时所有 IO 方法返回空/``False``，不影响其他模块（与现有
``main.py`` 的 ``HAS_MUTAGEN`` 约定一致）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Union

try:
    import mutagen

    HAS_MUTAGEN = True
except ImportError:  # pragma: no cover - 依赖缺失时的降级路径
    HAS_MUTAGEN = False

# easy 模式下字段名与常用语义映射（update 时的入参名 → 实际标签键）
_FIELD_MAP = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "year": "date",          # easy 模式用 date 承载年份
    "genre": "genre",
    "track": "tracknumber",
    "albumartist": "albumartist",
}


def available() -> bool:
    """mutagen 是否可用。"""
    return HAS_MUTAGEN


def _open_audio(path: str, easy: bool = True):
    """按扩展名打开标签对象，避免依赖音频帧存在（纯 ID3 文件也能处理）。

    - ``easy=True``: 返回 easy 兼容容器（MP3→EasyID3，其他→mutagen.File(easy=True)）。
    - ``easy=False``: 返回原生对象（MP3→ID3，其他→mutagen.File()）。
    - 不支持/不存在时抛异常，由调用方捕获。
    """
    ext = Path(path).suffix.lower()
    if ext == ".mp3":
        from mutagen.easyid3 import EasyID3
        from mutagen.id3 import ID3

        return EasyID3(path) if easy else ID3(path)
    f = mutagen.File(path, easy=easy)
    if f is None:
        raise ValueError(f"unsupported audio file: {path}")
    return f


# ── 艺术家拆分（纯函数）───────────────────────────────────────────────────

def split_artists(text: Union[str, List[str], None],
                  separators: Optional[str] = None) -> List[str]:
    """拆分多艺术家字符串。

    默认分隔符为中文顿号（``、``）、全/半角逗号（``,``/``，``）、
    斜杠（``/``）及分号（``;``/``；``）；传入 ``separators``（字符集合，
    如 ``"、,，"``）时改用自定义分隔符。``&``/``和``/``与`` 不拆分
    （通常是乐队名的一部分）。去重、去空白。
    """
    if text is None:
        return []
    if separators:
        pattern = "[" + "".join(re.escape(c) for c in separators) + "]"
    else:
        pattern = r"[、,，/;；]"
    if isinstance(text, list):
        pieces: List[str] = []
        for x in text:
            pieces.extend(re.split(pattern, str(x)))
    else:
        pieces = re.split(pattern, str(text))
    result: List[str] = []
    for p in pieces:
        p = p.strip()
        if p and p not in result:
            result.append(p)
    return result


# ── 读取 ──────────────────────────────────────────────────────────────────

def read_music_tags(path: str) -> Dict[str, list]:
    """读取音乐文件标签，返回 ``{字段: [值...]}``（值恒为列表）。"""
    if not HAS_MUTAGEN:
        return {}
    try:
        f = _open_audio(path, easy=True)
    except Exception:
        return {}
    out: Dict[str, list] = {}
    for key in ("title", "artist", "album", "date", "genre", "tracknumber", "albumartist"):
        try:
            if key in f:
                val = f[key]
                out[key] = list(val) if isinstance(val, (list, tuple)) else [str(val)]
        except Exception:
            continue
    return out


# ── 写入/更新 ─────────────────────────────────────────────────────────────

def update_music_tags(path: str, updates: Dict[str, Union[str, List[str], None]]) -> bool:
    """更新音乐文件标签。

    - ``updates`` 键为语义名（title/artist/album/year/genre/track/albumartist）。
    - 值为 ``str`` 或 ``List[str]``；``None``/空 表示删除该标签。
    - ``artist`` 传列表时按多值标签写入（媒体库可正确索引多艺人）。
    """
    if not HAS_MUTAGEN:
        return False
    try:
        f = _open_audio(path, easy=True)
    except Exception:
        return False
    try:
        for semantic, value in updates.items():
            tag_key = _FIELD_MAP.get(semantic, semantic)
            if value is None or (isinstance(value, (list, tuple)) and not value) \
                    or (isinstance(value, str) and not value.strip()):
                try:
                    del f[tag_key]
                except KeyError:
                    pass
            elif isinstance(value, (list, tuple)):
                f[tag_key] = [str(v) for v in value]
            else:
                f[tag_key] = [str(value)]
        f.save()
        return True
    except Exception:
        return False


# ── 封面管理 ──────────────────────────────────────────────────────────────

def read_cover(path: str) -> Optional[Dict[str, bytes]]:
    """读取已有封面，返回 ``{'mime': str, 'data': bytes}``；无则 ``None``。"""
    if not HAS_MUTAGEN:
        return None
    try:
        f = _open_audio(path, easy=False)
    except Exception:
        return None
    try:
        if hasattr(f, "pictures") and f.pictures:
            pic = f.pictures[0]
            return {"mime": pic.mime or "image/jpeg", "data": pic.data}
        apics = f.getall("APIC") if hasattr(f, "getall") else []
        if apics:
            apic = apics[0]
            return {"mime": apic.mime or "image/jpeg", "data": apic.data}
    except Exception:
        return None
    return None


def inject_cover(path: str, image_path: str) -> bool:
    """把图片文件作为封面写入音乐文件。"""
    try:
        data = Path(image_path).read_bytes()
    except OSError:
        return False
    lower = image_path.lower()
    mime = "image/jpeg" if lower.endswith((".jpg", ".jpeg")) else "image/png"
    return _inject_cover_bytes(path, data, mime)


def copy_cover(src_music: str, dst_music: str) -> bool:
    """从另一音乐文件复制封面到目标文件。"""
    cover = read_cover(src_music)
    if not cover:
        return False
    return _inject_cover_bytes(dst_music, cover["data"], cover["mime"])


def _inject_cover_bytes(path: str, data: bytes, mime: str) -> bool:
    if not HAS_MUTAGEN:
        return False
    try:
        f = _open_audio(path, easy=False)
    except Exception:
        return False
    try:
        if hasattr(f, "add_picture"):  # FLAC / OGG
            from mutagen.flac import Picture

            pic = Picture()
            pic.type = 3  # front cover
            pic.mime = mime
            pic.data = data
            f.add_picture(pic)
        elif hasattr(f, "add"):  # ID3 (MP3)
            from mutagen.id3 import APIC

            f.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        else:
            return False
        f.save()
        return True
    except Exception:
        return False
