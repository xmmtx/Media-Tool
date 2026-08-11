"""临时实证：真实平台下 libpng 警告来源（运行后删除）。

把 C 层 fd2 重定向到文件，覆盖 Qt qWarning 与 C 直接写 stderr 两条路径。
"""
import os
import sys

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)

# 先重定向 C 层 stderr 到文件
stderr_file = os.path.join(_root, "_tmp_libpng_stderr.txt")
logf = open(stderr_file, "wb", buffering=0)
_old_fd = os.dup(2)
os.dup2(logf.fileno(), 2)

try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    _orig_exec = QApplication.exec

    def _fake_exec(self, *a, **k):
        QTimer.singleShot(1500, lambda: QApplication.instance().quit())
        return _orig_exec()

    QApplication.exec = _fake_exec

    import main as m
    m.main()
finally:
    os.dup2(_old_fd, 2)
    logf.close()

content = open(stderr_file, encoding="utf-8", errors="replace").read()
print("LIBPNG_WARNING_PRESENT:", "libpng" in content.lower())
print("---- full captured stderr ----")
print(content)
