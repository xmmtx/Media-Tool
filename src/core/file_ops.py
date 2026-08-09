"""文件操作：Rename / Copy / Hardlink（跨盘自动回退）/ Undo 历史。

- :class:`FileOperator` 提供单文件操作并维护撤销历史。
- **Hardlink 跨盘策略**：Windows 上硬链接不能跨卷（``os.link`` 抛
  ``errno.EXDEV`` / cross-device），默认自动回退为拷贝并在结果中标记
  ``fallback_used=True``；配置 ``fallback="none"`` 时仅报错，交由上层 UI
  决定如何处理（如友好提示）。
- 撤销：rename → 改回原名；copy / hardlink → 删除目标（硬链接删除一个
  名字不影响原文件）。
"""

from __future__ import annotations

import errno
import os
import shutil
from dataclasses import dataclass, field
from typing import List, Optional


class FileOpError(Exception):
    """文件操作前置校验失败（源不存在 / 目标冲突等）。"""


@dataclass
class OperationResult:
    """一次文件操作的结果。"""

    op: str                      # rename | copy | hardlink
    src: str
    dst: str
    ok: bool = False
    error: Optional[str] = None
    fallback_used: bool = False  # hardlink 跨盘时是否已回退为拷贝


def _is_cross_device(src: str, dst: str) -> bool:
    """是否跨盘符（Windows 卷级判断；非 Windows 返回 False，交由 os.link 判定）。"""
    a = os.path.splitdrive(os.path.abspath(src))[0].lower()
    b = os.path.splitdrive(os.path.abspath(dst))[0].lower()
    return bool(a) and bool(b) and a != b


def _is_cross_device_error(e: OSError) -> bool:
    """判断 OSError 是否属于“跨设备/跨卷”错误。"""
    if getattr(e, "errno", None) in (errno.EXDEV,):
        return True
    text = str(e).lower()
    return any(k in text for k in (
        "cross-device", "cross device", "different drive",
        "another volume", "different volume",
    ))


class FileOperator:
    """文件操作器：执行 rename/copy/hardlink 并记录撤销历史。"""

    def __init__(
        self,
        fallback: str = "copy",       # hardlink 跨盘回退: "copy" | "none"
        overwrite: bool = False,      # 目标已存在时是否覆盖
        create_dirs: bool = True,     # 自动创建目标目录
    ) -> None:
        if fallback not in ("copy", "none"):
            raise ValueError(f"fallback must be 'copy' or 'none', got {fallback!r}")
        self.fallback = fallback
        self.overwrite = overwrite
        self.create_dirs = create_dirs
        self.history: List[tuple] = []  # (op, src, dst)

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _prepare(self, src: str, dst: str):
        src = os.path.abspath(src)
        dst = os.path.abspath(dst)
        if not os.path.exists(src):
            raise FileOpError(f"source not found: {src}")
        if os.path.exists(dst) and not self.overwrite:
            raise FileOpError(f"target already exists: {dst}")
        if self.create_dirs:
            parent = os.path.dirname(dst)
            if parent:
                os.makedirs(parent, exist_ok=True)
        return src, dst

    def _result(self, op: str, src: str, dst: str, e: Exception) -> OperationResult:
        return OperationResult(op, src, dst, error=str(e))

    # ── 操作 ──────────────────────────────────────────────────────────────

    def rename(self, src: str, dst: str) -> OperationResult:
        try:
            s, d = self._prepare(src, dst)
            os.rename(s, d)
            self.history.append(("rename", s, d))
            return OperationResult("rename", s, d, ok=True)
        except FileOpError as e:
            return self._result("rename", src, dst, e)
        except OSError as e:
            return self._result("rename", src, dst, e)

    def copy(self, src: str, dst: str) -> OperationResult:
        try:
            s, d = self._prepare(src, dst)
            shutil.copy2(s, d)
            self.history.append(("copy", s, d))
            return OperationResult("copy", s, d, ok=True)
        except FileOpError as e:
            return self._result("copy", src, dst, e)
        except OSError as e:
            return self._result("copy", src, dst, e)

    def hardlink(self, src: str, dst: str) -> OperationResult:
        """创建硬链接；跨盘（EXDEV）时按 ``fallback`` 配置自动回退为拷贝。"""
        try:
            s, d = self._prepare(src, dst)
        except FileOpError as e:
            return self._result("hardlink", src, dst, e)
        except OSError as e:
            return self._result("hardlink", src, dst, e)
        try:
            if _is_cross_device(s, d):
                raise OSError(errno.EXDEV, "Cross-device link (different volume)")
            os.link(s, d)
        except OSError as e:
            if _is_cross_device_error(e) and self.fallback == "copy":
                try:
                    shutil.copy2(s, d)
                except OSError as ce:
                    return self._result("hardlink", s, d, ce)
                self.history.append(("copy", s, d))
                return OperationResult("hardlink", s, d, ok=True, fallback_used=True)
            if _is_cross_device_error(e):
                return OperationResult(
                    "hardlink", s, d,
                    error="cross-device hardlink not supported and fallback disabled",
                )
            return self._result("hardlink", s, d, e)
        self.history.append(("hardlink", s, d))
        return OperationResult("hardlink", s, d, ok=True)

    # ── 撤销 ──────────────────────────────────────────────────────────────

    def undo_last(self) -> bool:
        """撤销最近一次成功操作，返回是否成功。"""
        if not self.history:
            return False
        op, s, d = self.history[-1]
        try:
            if op == "rename":
                if os.path.exists(d):
                    os.rename(d, s)
            elif op in ("copy", "hardlink"):
                if os.path.exists(d):
                    os.unlink(d)
            else:
                return False
            self.history.pop()
            return True
        except OSError:
            return False

    def undo_all(self) -> int:
        """撤销全部操作，返回撤销次数。"""
        n = 0
        while self.undo_last():
            n += 1
        return n
