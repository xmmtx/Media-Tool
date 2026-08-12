"""酷狗加密音乐解锁：调用官方 Unlock Music CLI（um.exe）解密 KGG/KGM 文件。

策略：
- ``um.exe`` 定位：安装版（PyInstaller frozen）找 exe 同目录捆绑的 ``um.exe``；
  开发版依次找 ``reference/um-cli/um.exe``、``reference/um.exe``，最后兜底 PATH。
- KGG v5 密钥数据库：优先用设置页配置的路径（``music.kugou_db``）；留空则自动
  检测 ``%APPDATA%\\Kugou8\\KGMusicV3.db``；两者都无效时解密报错并提示手动配置。
- 解密输出写回源文件所在目录（文件名去加密后缀 + 探测出的音频扩展名），
  解密成功即删除加密源文件（CLI ``--remove-source``），符合「转换后放在原目录」。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("core.music_unlock")

# 酷狗加密音频扩展名（需先解密才能进媒体库 pipeline）
KUGOU_EXTS = {".kgm", ".vpr", ".kgg", ".kgma"}

# KGG v5 密钥数据库默认路径（未配置时自动检测）
_KUGOU_DB_DEFAULT = os.path.join(os.environ.get("APPDATA", ""), "Kugou8", "KGMusicV3.db")

# 解密超时（秒）
_DECRYPT_TIMEOUT = 300


class UnlockError(Exception):
    """酷狗解锁失败；``message`` 为用户可读的错误信息（显示在错误列/人工队列）。"""


def _unlock_cmd() -> Optional[str]:
    """定位 um.exe：frozen → exe 同目录；开发 → reference；最后 PATH。"""
    if getattr(sys, "frozen", False):  # PyInstaller：exe 同目录捆绑 um.exe
        local = os.path.join(os.path.dirname(sys.executable), "um.exe")
        if os.path.isfile(local):
            return local
    root = Path(__file__).resolve().parents[3]  # 项目根
    for cand in (root / "reference" / "um-cli" / "um.exe",
                 root / "reference" / "um.exe"):
        if cand.is_file():
            return str(cand)
    found = shutil.which("um")
    if found:
        return found
    return None


def resolve_kugou_db(configured: str = "") -> Optional[str]:
    """返回有效的酷狗密钥数据库路径；无则 ``None``（触发解密报错提示手动配置）。"""
    for p in (configured, _KUGOU_DB_DEFAULT):
        if p and os.path.isfile(p):
            return p
    return None


def decrypt_kugou(path: str, kugou_db: str = "") -> List[str]:
    """解密单个酷狗加密文件，返回解密后的明文音频文件路径列表（通常一个）。

    - 非酷狗加密扩展名直接返回空列表。
    - 输出写到源文件所在目录，成功后删除加密源文件。
    - 失败抛 :class:`UnlockError`，``message`` 为用户可读中文错误。
    """
    if os.path.splitext(path)[1].lower() not in KUGOU_EXTS:
        return []

    cmd = _unlock_cmd()
    if not cmd:
        raise UnlockError("未找到音乐解锁工具（um.exe），请确认其已随应用安装")

    ext = os.path.splitext(path)[1].lower()
    db = resolve_kugou_db(kugou_db)
    if not db and ext == ".kgg":  # KGG v5 必须密钥数据库；KGM v1-4 (.kgm/.vpr/.kgma) 无需
        raise UnlockError(
            "未找到酷狗密钥数据库（KGMusicV3.db），请到设置页「酷狗密钥数据库路径」手动配置")

    out_dir = os.path.dirname(os.path.abspath(path))
    before = set(os.listdir(out_dir))
    args = [cmd, "-o", out_dir, "--remove-source", path]
    if db:  # 配置了/检测到 db 才传，否则让 CLI 用内置默认路径
        args[1:1] = ["--kgg-db", db]
    logger.info("酷狗解密: %s -> %s (db=%s)", os.path.basename(path), out_dir, db)
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=_DECRYPT_TIMEOUT,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise UnlockError("未找到音乐解锁工具（um.exe），请确认其已随应用安装")
    except subprocess.TimeoutExpired:
        raise UnlockError(f"酷狗解密超时：{os.path.basename(path)}")

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or "unknown error"
        logger.error("酷狗解密失败 %s: %s", path, detail)
        raise UnlockError(f"酷狗解密失败：{detail[:300]}")

    # 目录快照对比取本次生成的明文文件（源加密文件已删除，也不会误收其他同名前缀）
    new_files = set(os.listdir(out_dir)) - before
    found = [os.path.join(out_dir, n) for n in new_files
             if os.path.splitext(n)[1].lower() not in KUGOU_EXTS]
    if not found:
        raise UnlockError(f"酷狗解密未生成输出文件：{os.path.basename(path)}")
    logger.info("酷狗解密完成: %s", [os.path.basename(f) for f in found])
    return found
