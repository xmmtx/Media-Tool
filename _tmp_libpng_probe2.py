"""临时实证2：不装 handler，真实平台加载 MainWindow，看 libpng 是否出现（运行后删除）。"""
import os
import sys

_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, "src"))

stderr_file = os.path.join(_root, "_tmp_libpng_stderr2.txt")
logf = open(stderr_file, "wb", buffering=0)
_old_fd = os.dup(2)
os.dup2(logf.fileno(), 2)

try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from ui.main_window import enable_high_dpi, MainWindow

    enable_high_dpi()
    app = QApplication(sys.argv)
    QTimer.singleShot(1500, app.quit)
    w = MainWindow()
    w.show()
    app.exec()
finally:
    os.dup2(_old_fd, 2)
    logf.close()

content = open(stderr_file, encoding="utf-8", errors="replace").read()
lines = [l for l in content.splitlines() if l.strip()]
print("LIBPNG_WARNING_PRESENT:", "libpng" in content.lower())
print("STDERR_LINES:", len(lines))
for l in lines[:40]:
    print(" |", l)
