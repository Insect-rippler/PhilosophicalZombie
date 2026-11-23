"""PhilosophicalZombie 桌面端骨架（PySide6）。

主会话 / 通信设备 / 识域 / 技能 / 设置。1440×900 基准，
背景图铺满整页；按钮透明无边框，容器透明底 + 细边框。

    python ui/app.py

后端要先跑：python engine/server.py
"""

import json
import math
import os
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QThread, QTimer, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QPlainTextEdit,
                               QPushButton, QGroupBox, QScrollArea, QSizePolicy, QSplitter, QGraphicsDropShadowEffect,
                               QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

BASE = os.environ.get("PZ_SERVER", "http://127.0.0.1:8765")
ASSET_DIR = Path(__file__).resolve().parent / "assets"
BACKDROP_IMAGE = ASSET_DIR / "background.png"
BUILD = "b1119-1"
ACCENT = "#2f2f2f"
PICKER_QSS = """
QMenu { background: #1a1713; border: 1px solid rgba(242,232,213,0.30); padding: 6px; color: #f2e8d5; }
QMenu::item { padding: 7px 26px 7px 12px; }
QMenu::item:selected { background: rgba(242,232,213,0.12); }
QMenu::item:disabled { color: #8f8370; }
QMenu::separator { height: 1px; background: rgba(242,232,213,0.20); margin: 6px 8px; }
"""
PAPER = "#0d0b09"
LINE = "rgba(242,232,213,0.28)"
MUTED = "#c8b89f"
ACCENT = "#e7c58a"

QSS = f"""
QMainWindow, QDialog {{ background: {PAPER}; }}
QWidget {{ background: transparent; color: #f2e8d5; font-size: 13.5px; }}
QLabel#h1 {{ color: #f7ecd8; font-size: 19px; font-weight: 600; letter-spacing: 2px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#info {{ color: #b9aa94; font-size: 11.5px; }}
QLabel#narration {{ color: #f6e2b4; background: transparent; border: none;
                    padding: 4px 12px; }}
QLabel#group {{ color: #f3e6cf; font-size: 12.5px; font-weight: 600;
                letter-spacing: 1px; }}
QLabel#fieldName {{ color: #f0e4d0; }}
QLineEdit {{ color: #f6ecd8; placeholder-text-color: #cdbb9f; }}
QLabel#tipItem {{ color: #c6b59d; }}
QLabel#stamp {{ color: #b6a58c; font-size: 10px; }}
QLabel#clock {{ color: #f8e9c8; font-size: 16px; font-weight: 600; padding: 6px 16px;
               background: transparent; border: 1px solid rgba(242,232,213,0.30);
               border-radius: 14px; }}
QLabel#usage {{ color: #e5d3b5; font-size: 11.5px; background: transparent; }}
QFrame#card {{ background: transparent; border: 1px solid {LINE}; border-radius: 8px; }}
QFrame#chatShell {{ background: transparent; border: 1px solid rgba(242,232,213,0.38);
                    border-radius: 10px; }}
QFrame#inputShell {{ background: transparent; border: 1px solid rgba(242,232,213,0.34);
                     border-radius: 8px; }}
QFrame#usageBar {{ background: transparent; border: 1px solid rgba(242,232,213,0.26);
                   border-radius: 8px; }}
QFrame#msgCard {{ background: transparent; border: 1px solid rgba(242,232,213,0.42);
                  border-radius: 10px; }}
QFrame#msgCard QLabel {{ background: transparent; border: none; }}
QFrame#tipBox {{ background: transparent; border: none; }}
QPushButton {{ border: none; background: transparent; color: #f0e3cc;
               padding: 6px 12px; }}
QPushButton:hover {{ color: #ffffff; }}
QPushButton#nav {{ border: none; background: transparent; color: {MUTED}; }}
QPushButton#nav:checked {{ background: transparent; color: #f7e7c4; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox {{
    border: none; background: transparent; padding: 7px 9px; color: #f6ecd8;
    selection-background-color: rgba(231,197,138,0.35); }}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{ background: #1a1713; border: 1px solid {LINE};
    color: #f2e8d5; selection-background-color: rgba(242,232,213,0.14); }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(242,232,213,0.24); border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QListWidget {{ border: 1px solid {LINE}; background: transparent; border-radius: 6px; }}
QListWidget::item {{ padding: 8px 10px; }}
QListWidget::item:selected {{ background: rgba(242,232,213,0.12); color: #fff6e4; }}
QPushButton#tipHeader {{ border: none; background: transparent; color: #b9aa94;
                         padding: 2px 4px; text-align: left; font-size: 11px; }}
QPushButton#tipHeader:hover {{ color: #f4e4c4; background: transparent; }}
QPushButton#picker {{ border: none; padding: 2px 8px; background: transparent;
                      color: #d9c7a7; }}
QPushButton#winBtn {{ border: none; padding: 0; background: transparent;
                      color: #f5e6c8; font-size: 16px; font-weight: 600; }}
QPushButton#winBtn:hover {{ background: rgba(242,232,213,0.12); color: #fff6e4; }}
QPushButton#winClose {{ border: none; padding: 0; background: transparent;
                       color: #f5e6c8; font-size: 16px; font-weight: 600; }}
QPushButton#winClose:hover {{ background: rgba(190,70,55,0.38); color: #fff; }}
QCheckBox {{ color: #eadcc7; }}
QGroupBox {{ border: 1px solid {LINE}; border-radius: 6px; margin-top: 10px;
             padding-top: 8px; color: #f2e6d0; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px;
                    color: #e7d6ba; }}
"""

# ---------------- 网络 ----------------

class TurnWorker(QThread):
    """一轮对话：读 SSE，逐块发出去。"""

    delta = Signal(str)
    done = Signal(str)
    failed = Signal(str)
    tool = Signal(dict)
    usage = Signal(dict)

    def __init__(self, text):
        super().__init__()
        self.text = text

    def run(self):
        import requests
        import urllib3
        urllib3.disable_warnings()
        parts = []
        try:
            with requests.post(f"{BASE}/turn", json={"text": self.text},
                               stream=True, timeout=(10, 600),
                               proxies={"http": None, "https": None}) as resp:
                if resp.status_code != 200:
                    self.failed.emit(f"服务返回 {resp.status_code}：{resp.text[:120]}")
                    return
                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if line.startswith("event: "):
                        self._kind = line[7:].strip()
                    elif line.startswith("data: "):
                        payload = line[6:]
                        kind = getattr(self, "_kind", "delta")
                        try:
                            value = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if kind == "tool":
                            self.tool.emit(value)
                        elif kind == "usage":
                            self.usage.emit(value)
                        elif kind == "delta":
                            parts.append(value)
                            self.delta.emit(value)
                        elif kind == "done":
                            self._finished = True
                            self.done.emit(value)
                        elif kind == "error":
                            self.failed.emit(str(value))
        except Exception as e:               # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            if parts and not getattr(self, "_finished", False):
                self.done.emit("".join(parts))


class ViewWorker(QThread):
    """长连接 /events；只把后端算好的 view 交给界面。"""

    view = Signal(dict)
    stream = Signal(dict)
    done = Signal(dict)
    tool = Signal(dict)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
        self._resp = None

    def stop(self):
        self._stop = True
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:                    # noqa: BLE001
            pass

    def run(self):
        import time as _t
        import requests
        import urllib3
        urllib3.disable_warnings()
        while not self._stop:
            try:
                with requests.get(f"{BASE}/events", stream=True, timeout=(5, 40),
                                  proxies={"http": None, "https": None}) as resp:
                    self._resp = resp
                    if resp.status_code != 200:
                        _t.sleep(2)
                        continue
                    kind = "message"
                    for line in resp.iter_lines(decode_unicode=True):
                        if self._stop:
                            break
                        if not line:
                            continue
                        if line.startswith("event: "):
                            kind = line[7:].strip()
                        elif line.startswith("data: "):
                            try:
                                value = json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue
                            if kind == "view":
                                self.view.emit(value)
                            elif kind == "stream":
                                self.stream.emit(value)
                            elif kind == "done":
                                self.done.emit(value)
                            elif kind == "tool":
                                self.tool.emit(value)
                            elif kind == "error":
                                self.error.emit(str(value.get("message") if isinstance(value, dict)
                                                     else value))
            except Exception:                # noqa: BLE001
                if not self._stop:
                    _t.sleep(2)
            finally:
                self._resp = None


def api_get(path):
    import requests
    import urllib3
    urllib3.disable_warnings()
    return requests.get(f"{BASE}{path}", timeout=10,
                        proxies={"http": None, "https": None}).json()


def api_send(method, path, data=None):
    import requests
    import urllib3
    urllib3.disable_warnings()
    return requests.request(method, f"{BASE}{path}", json=data, timeout=15,
                            proxies={"http": None, "https": None}).json()


# ---------------- 对话页 ----------------

LIMIT_CHARS = 90


NO_LINE_START = set("，。、；：？！）〕】》」』〉”’…‥·ー～,.;:?!)]}%‰℃°％")
NO_LINE_END = set("（〔【《「『〈“‘([{＄$￥")
NO_BREAK_UNITS = set("年月日时分秒元角斤两米克吨度岁次天周号点%％")


def _shift_break(block, cut):
    """把折点往前挪，直到行首、行尾都不违反禁则。"""
    if cut >= len(block):
        return cut
    while cut > 1:
        nxt, prev = block[cut], block[cut - 1]
        if nxt in NO_LINE_START or prev in NO_LINE_END:
            cut -= 1
            continue
        # 数字与单位不拆开
        if nxt in NO_BREAK_UNITS and prev.isdigit():
            cut -= 1
            continue
        # 英文与数字不拆词
        if prev.isascii() and nxt.isascii() and prev.isalnum() and nxt.isalnum():
            cut -= 1
            continue
        break
    return cut


def wrap_to_px(text, fm, limit_px):
    """按像素贪心折行，并做中文避头尾与数字/单位保护。"""
    lines = []
    for block in str(text).split("\n"):
        block = block.strip()
        while block:
            cut = len(block)
            while cut > 1 and fm.horizontalAdvance(block[:cut]) > limit_px:
                cut -= 1
            cut = _shift_break(block, cut)
            lines.append(block[:cut])
            block = block[cut:]
    return "\n".join(lines)


def wrap_chars(text, width=LIMIT_CHARS):
    """按字符数硬折行：每行不超过 width 个字。"""
    lines = []
    for block in str(text).split("\n"):
        block = block.strip()
        if not block:
            continue
        for i in range(0, len(block), width):
            lines.append(block[i:i + width])
    return "\n".join(lines)


class Backdrop(QWidget):
    """纸面装饰：从顶边垂下的藤蔓，像窗前的植物架。"""

    INK = QColor("#d9d2c4")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.picture = QPixmap(str(BACKDROP_IMAGE)) if BACKDROP_IMAGE.exists() else QPixmap()

    def _draw_picture(self, painter):
        """背景图铺满整个窗口，压一层暗色保证文字可读。"""
        painter.fillRect(self.rect(), QColor(PAPER))
        if not self.picture.isNull():
            scaled = self.picture.scaled(self.size(), Qt.KeepAspectRatioByExpanding,
                                         Qt.SmoothTransformation)
            painter.setOpacity(1.0)
            painter.drawPixmap((self.width() - scaled.width()) // 2,
                               (self.height() - scaled.height()) // 2, scaled)
        # 上下压暗、中间留亮；让顶部胶囊和底部输入在亮背景上也能读
        shade = QLinearGradient(0, 0, 0, max(1, self.height()))
        shade.setColorAt(0.0, QColor(8, 7, 6, 76))
        shade.setColorAt(0.42, QColor(8, 7, 6, 18))
        shade.setColorAt(0.72, QColor(8, 7, 6, 70))
        shade.setColorAt(1.0, QColor(8, 7, 6, 188))
        painter.fillRect(self.rect(), shade)
        painter.end()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._draw_picture(painter)

    def _hang(self, painter, x, length, sway, leaves=5):
        """从顶边垂下来的一串：一条弯曲主茎，交错的叶子，末端一朵小花。"""
        path = QPainterPath()
        path.moveTo(x, 0)
        spans = leaves + 1
        for index in range(spans):
            y1 = length * index / spans
            y2 = length * (index + 1) / spans
            offset = sway if index % 2 == 0 else -sway
            path.cubicTo(x + offset, y1, x + offset, y2, x, y2)
        painter.drawPath(path)

        for index in range(1, spans):
            y = length * index / spans
            self._leaf(painter, x, y, 8 if index % 2 else -8, index)

        self._flower(painter, x, length + 4, 6.5)

    def _leaf(self, painter, x, y, direction, index):
        size = 7 if index % 2 else 9
        path = QPainterPath()
        path.moveTo(x, y)
        path.quadTo(x + direction * 1.3, y - size * 0.7, x + direction * 2.3, y + 1)
        path.quadTo(x + direction * 1.2, y + size * 0.6, x, y)
        painter.drawPath(path)

    def _flower(self, painter, cx, cy, size):
        import math
        for index in range(5):
            angle = index * 72 * math.pi / 180
            px = cx + size * 0.85 * math.cos(angle)
            py = cy + size * 0.85 * math.sin(angle)
            painter.drawEllipse(int(px - size * 0.5), int(py - size * 0.5),
                                int(size), int(size))


def _text_shadow(widget):
    """给压在背景图上的文字一点暗影，避免和亮部糊在一起。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(7)
    effect.setOffset(0, 1)
    effect.setColor(QColor(0, 0, 0, 210))
    widget.setGraphicsEffect(effect)


class Bubble(QFrame):
    """一条消息。透明容器；正文按段落拆成白底子容器。"""

    def __init__(self, role, text, media=None, ts="", split=True, agent_name="", user_name=""):
        super().__init__()
        self.role = role
        self.bodies = []
        self.setObjectName("msgCard" if role not in ("narration", "tip") else "plain")

        layout = QVBoxLayout(self)
        if role in ("narration", "tip"):
            layout.setContentsMargins(0, 0, 0, 0)
        else:
            layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(6)

        if role == "narration":
            label = QLabel(text)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignCenter)
            label.setObjectName("narration")
            font = label.font()
            font.setItalic(True)
            font.setPointSizeF(max(9.0, font.pointSizeF() - 0.5))
            label.setFont(font)
            label.ensurePolished()
            metrics = QFontMetrics(label.font())
            natural = metrics.horizontalAdvance(str(text)) + 44
            label.setProperty("narration", True)
            _text_shadow(label)
            layout.setContentsMargins(0, 2, 0, 2)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(label)
            row.addStretch(1)
            layout.addLayout(row)
            return

        if role == "tip":
            label = QLabel(f"  {text}")
            label.setObjectName("muted")
            font = label.font()
            font.setPointSizeF(max(8.5, font.pointSizeF() - 1.5))
            label.setFont(font)
            layout.addWidget(label)
            return

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        if role == "user":
            display_name = user_name or "无名者"
        elif role == "assistant":
            display_name = agent_name or "无名者"
        else:
            display_name = role
        name = QLabel(display_name)
        self.name_label = name
        name.setObjectName("muted")
        top.addWidget(name)
        stamp = QLabel(str(ts)[-8:-3] if ts else "")
        stamp.setObjectName("stamp")
        top.addWidget(stamp)
        top.addStretch(1)
        layout.addLayout(top)

        if split:
            paragraphs = [block.strip() for block in str(text).split("\n") if block.strip()]
        else:
            paragraphs = [str(text)]
        if not paragraphs:
            paragraphs = [""]
        for paragraph in paragraphs:
            body = QLabel(paragraph)
            body.setWordWrap(False)
            body.setProperty("raw", paragraph)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.setObjectName("bubbleBody")
            body.setStyleSheet("QLabel#bubbleBody { background: transparent;"
                               " border: none; padding: 2px 0; color: #f4ead6; }")
            _text_shadow(body)
            layout.addWidget(body)
            self.bodies.append(body)

        for path in media or []:
            if not Path(path).exists():
                continue
            if Path(path).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            picture = QLabel()
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                picture.setPixmap(pixmap.scaledToWidth(280, Qt.SmoothTransformation))
                layout.addWidget(picture)


class LockOverlay(QWidget):
    """输入区忙碌遮罩：两道交叉锁链，禁止输入。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setCursor(Qt.ForbiddenCursor)
        self.setToolTip("后端正在运行，稍等")

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(8, 7, 6, 138))
        w, h = self.width(), self.height()
        pen = QPen(QColor(228, 198, 137, 235), 4.0, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        for x1, y1, x2, y2 in ((0, 0, w, h), (w, 0, 0, h)):
            p.drawLine(x1, y1, x2, y2)
            length = math.hypot(x2 - x1, y2 - y1)
            steps = max(1, int(length // 22))
            for i in range(1, steps):
                t = i / steps
                cx, cy = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
                p.setBrush(QColor(8, 7, 6, 220))
                p.drawEllipse(QPoint(int(cx), int(cy)), 5, 3)
        p.setPen(QPen(QColor(244, 234, 214, 245), 1))
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(max(9.0, font.pointSizeF() + 0.5))
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignCenter, "运行中")


class TipGroup(QFrame):
    """一轮里的工具调用。调用中逐条显示，结束后折叠成一行。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tipBox")
        self.setStyleSheet("QFrame#tipBox { border: none; background: transparent; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)

        self.header = QPushButton()
        self.header.setFlat(True)
        self.header.setObjectName("tipHeader")
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.clicked.connect(self.toggle)
        layout.addWidget(self.header)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(1)
        layout.addWidget(self.body)

        self.count = 0
        self.expanded = False
        self.body.hide()
        self.set_header()

    def set_header(self):
        if self.count == 0:
            self.header.setText("· 调用中…")
        else:
            self.header.setText(f"· {self.count} 次工具调用 {'▾' if self.expanded else '▸'}")

    def add(self, text):
        label = QLabel(f"　{text}")
        label.setObjectName("tipItem")
        f = label.font()
        f.setPointSizeF(max(8.5, f.pointSizeF() - 1.5))
        label.setFont(f)
        self.body_layout.addWidget(label)
        self.count += 1
        self.set_header()

    def collapse(self):
        self.expanded = False
        self.body.hide()
        self.set_header()

    def toggle(self):
        self.expanded = not self.expanded
        self.body.setVisible(self.expanded)
        self.set_header()


class HeaderBar(QWidget):
    """顶栏：左侧位置/状态，右侧最短计时器胶囊。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(58)
        self.clock = QLabel("◷ --:--", self)
        self.clock.setObjectName("clock")
        self.clock.setMinimumWidth(118)
        self.clock.setAlignment(Qt.AlignCenter)
        self.clock.setToolTip("最短的计时器")

class StatePanel(QFrame):
    """状态抽屉（当前不常显，留作以后用）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 16, 16)
        title = QLabel("状态")
        title.setObjectName("h1")
        layout.addWidget(title)
        self.body = QLabel("—")
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignTop)
        layout.addWidget(self.body, 1)

    def update_text(self, text):
        self.body.setText(text)


class ChatPage(QWidget):
    def __init__(self):
        super().__init__()
        self.model_name = "DeepSeek-V4.1-Flash"
        self.think_level = "Max"
        self.worker = None
        self._hide_timer = None
        self._tip_group = None
        self._buffer = ""
        from PySide6.QtCore import QTimer as _QT
        self._clock_timer = _QT(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start()
        self._identity_timer = _QT(self)
        self._identity_timer.setInterval(2000)
        self._identity_timer.timeout.connect(self._refresh_identity)
        self._identity_timer.start()

        self._view_worker = ViewWorker(self)
        self._view_worker.view.connect(self._on_view)
        self._view_worker.stream.connect(self._on_stream)
        self._view_worker.done.connect(self._on_stream_done)
        self._view_worker.tool.connect(self._on_tool)
        self._view_worker.error.connect(self._on_stream_error)
        self._view_worker.start()
        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence("F5"), self, activated=self.reload_all)
        self._seen = 0

        row_outer = QHBoxLayout(self)
        row_outer.setContentsMargins(0, 0, 0, 0)
        row_outer.setSpacing(0)

        outer = QVBoxLayout()
        outer.setContentsMargins(28, 18, 28, 14)
        outer.setSpacing(10)

        self.shell = QFrame()
        self.shell.setObjectName("chatShell")
        shell_box = QVBoxLayout(self.shell)
        shell_box.setContentsMargins(14, 11, 14, 11)
        shell_box.setSpacing(8)

        self.header = HeaderBar()
        head = QHBoxLayout(self.header)
        head.setContentsMargins(4, 0, 4, 0)
        head.setSpacing(8)
        self.info = QLabel("", self.header)
        self.info.setObjectName("info")
        self.info.setWordWrap(True)
        self.info.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.info.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        _text_shadow(self.info)
        head.addWidget(self.info, 1)
        head.addWidget(self.header.clock)
        shell_box.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.stack = QWidget()
        self.messages = QVBoxLayout(self.stack)
        self.messages.setSpacing(10)
        self.messages.addStretch(1)
        self.scroll.setWidget(self.stack)
        self.scroll.setMinimumSize(0, 0)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.scroll.viewport().setAutoFillBackground(False)
        self.stack.setAutoFillBackground(False)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._follow = True
        self._vbar = self.scroll.verticalScrollBar()
        self._vbar.rangeChanged.connect(self._on_range)
        self._vbar.valueChanged.connect(self._on_scroll)
        self.scroll.setContextMenuPolicy(Qt.CustomContextMenu)
        self.scroll.customContextMenuRequested.connect(self._chat_menu)
        shell_box.addWidget(self.scroll, 1)

        input_shell = QFrame()
        self.input_shell = input_shell
        input_shell.setObjectName("inputShell")
        input_row = QHBoxLayout(input_shell)
        input_row.setContentsMargins(10, 2, 6, 2)
        input_row.setSpacing(4)
        self.input = QLineEdit()
        self.input.setPlaceholderText("说点什么…")
        self.input.returnPressed.connect(self.send)
        send_btn = QPushButton("发送")
        self.send_btn = send_btn
        send_btn.setCursor(Qt.PointingHandCursor)
        send_btn.clicked.connect(self.send)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(send_btn)
        shell_box.addWidget(input_shell)
        self.lock_overlay = LockOverlay(input_shell)
        self.lock_overlay.setGeometry(input_shell.rect())
        self.lock_overlay.hide()

        usage_bar = QFrame()
        usage_bar.setObjectName("usageBar")
        self.usage_bar = usage_bar
        usage_row = QHBoxLayout(usage_bar)
        usage_row.setContentsMargins(10, 2, 8, 2)
        usage_row.setSpacing(10)
        self.usage = QLabel("usage —")
        self.usage.setObjectName("usage")
        self.pending_label = QLabel("")
        self.pending_label.setObjectName("muted")
        self.model_btn = QPushButton()
        self.model_btn.setObjectName("picker")
        self.model_btn.setCursor(Qt.PointingHandCursor)
        self.model_btn.clicked.connect(self.open_picker)
        self.think_btn = QPushButton()
        self.think_btn.setObjectName("picker")
        self.think_btn.setCursor(Qt.PointingHandCursor)
        self.think_btn.setToolTip("思考强度")
        self.think_btn.clicked.connect(self.open_think_picker)
        usage_row.addWidget(self.usage)
        usage_row.addWidget(self.pending_label)
        usage_row.addStretch(1)
        usage_row.addWidget(self.model_btn)
        usage_row.addSpacing(8)
        usage_row.addWidget(self.think_btn)
        usage_row.addSpacing(4)
        self._win_min = QPushButton("−")
        self._win_max = QPushButton("□")
        self._win_close = QPushButton("×")
        for btn, tip in ((self._win_min, "最小化"), (self._win_max, "最大化"),
                         (self._win_close, "关闭")):
            btn.setObjectName("winBtn")
            btn.setFixedSize(28, 24)
            btn.setFlat(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            usage_row.addWidget(btn)
        self._win_close.setObjectName("winClose")
        self._win_min.clicked.connect(lambda: self.window().showMinimized())
        self._win_max.clicked.connect(self._toggle_max)
        self._win_close.clicked.connect(lambda: self.window().close())
        shell_box.addWidget(usage_bar)

        outer.addWidget(self.shell, 1)
        holder = QWidget()
        holder.setLayout(outer)
        row_outer.addWidget(holder, 1)

        self.panel = StatePanel(self)
        self.panel.hide()
        self._last_message_at = 0
        self.agent_name = "无名者"
        self.user_name = "无名者"
        self._locked = False
        self._timer_targets = []
        self._silence_at = 0
        self._idle_enabled = True
        self._idle_minutes = 30.0
        self._view_timer_id = None
        self._view_timer_name = ""
        self._timer_deadline = 0.0
        self._sync_pending = False
        self._stream_active = False
        self._stream_id = ""
        self._stream_bubble = None
        self._stream_label = None
        self._buffer = ""
        self._pending_user = None
        self.refresh_state()
        self.refresh_view()
        self._tick_clock()

        self.refresh_model_text()

    def shutdown(self):
        try:
            self._view_worker.stop()
            if not self._view_worker.wait(2500):
                self._view_worker.terminate()
                self._view_worker.wait(500)
        except Exception:                    # noqa: BLE001
            pass

    def refresh_view(self):
        try:
            view = api_get("/view")
        except Exception:                    # noqa: BLE001
            return
        self._apply_view(view, force=True)

    def _on_view(self, view):
        self._apply_view(view)

    def _apply_view(self, view, force=False):
        if not isinstance(view, dict):
            return
        st = view.get("state") or {}
        location = st.get("location") or "未知"
        status = st.get("status") or "醒着"
        self.info.setText(f"角色位置：{location}\n状态：{status}")
        self.info.setToolTip(f"角色位置：{location}\n状态：{status}")
        usage = (view.get("usage") or {}).get("text")
        if usage:
            self.usage.setText(str(usage))
        if "agentName" in view:
            self.agent_name = str(view.get("agentName") or "").strip() or "无名者"
        if "userName" in view:
            self.user_name = str(view.get("userName") or "").strip() or "无名者"
        self._update_bubble_names()
        self._apply_timer(view.get("timer"))
        if not force and (self._stream_active
                          or (self.worker and self.worker.isRunning())):
            return
        messages = list(view.get("messages") or [])
        if self._pending_user:
            pending = str(self._pending_user.get("text") or "")
            canonical = [str(m.get("text") or "") for m in messages
                         if m.get("kind") == "user"]
            if pending and pending not in canonical:
                messages.append({"id": "local-user", "kind": "user",
                                 "text": pending, "ts": ""})
            elif pending:
                self._pending_user = None
        self._render_view_messages(messages)

    def _on_stream(self, payload):
        if not isinstance(payload, dict):
            return
        sid = str(payload.get("id") or "")
        delta = str(payload.get("delta") or "")
        if not delta:
            return
        if not self._stream_active or sid != self._stream_id or self._stream_bubble is None:
            self._stream_active = True
            self._stream_id = sid
            self._buffer = ""
            self.add_bubble("assistant", "", split=False)
            self._stream_bubble = self.messages.itemAt(self.messages.count() - 2).widget()
            labels = self._stream_bubble.findChildren(QLabel) if self._stream_bubble else []
            self._stream_label = labels[-1] if labels else None
        self._buffer += delta
        if self._stream_label is not None:
            self._stream_label.setText(wrap_chars(self._buffer))
        if self._stream_bubble is not None:
            self._fit_width(self._stream_bubble)
        if self._follow:
            self._vbar.setValue(self._vbar.maximum())

    def _on_stream_done(self, payload):
        if isinstance(payload, dict):
            text = str(payload.get("text") or "")
            if self._stream_label is not None and text:
                self._stream_label.setText(wrap_chars(text))
        self._stream_active = False
        self._stream_bubble = None
        self._stream_label = None

    def _on_stream_error(self, message):
        self._stream_active = False
        self.add_bubble("narration", f"[连接出错] {message}")
        self.refresh_view()

    def _apply_timer(self, timer):
        import time as _t
        if not timer:
            self._view_timer_id = None
            self._view_timer_name = ""
            self._timer_deadline = 0.0
            return
        try:
            remain = float(timer.get("remainingMs") or 0) / 1000.0
            grace = max(float(timer.get("graceMs") or 0) / 1000.0, 3.0)
        except (TypeError, ValueError):
            remain, grace = 0.0, 3.0
        self._view_timer_id = timer.get("id")
        self._view_timer_name = str(timer.get("label") or "")
        self._timer_deadline = _t.monotonic() + remain + grace

    def _sync_timer(self):
        if self._sync_pending:
            return
        self._sync_pending = True
        try:
            data = api_send("POST", "/sync", {"timerId": self._view_timer_id})
            if isinstance(data, dict) and isinstance(data.get("view"), dict):
                self._apply_view(data["view"], force=True)
        except Exception:                    # noqa: BLE001
            pass
        finally:
            self._sync_pending = False

    def _render_view_messages(self, messages):
        self._clear_bubbles()
        group = None
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            kind = str(msg.get("kind") or "")
            ts = str(msg.get("ts") or "")
            if kind == "tip":
                if group is None:
                    group = TipGroup()
                    self.messages.insertWidget(self.messages.count() - 1, group)
                for item in msg.get("items") or []:
                    if isinstance(item, dict):
                        group.add(str(item.get("text") or ""))
                continue
            group = None
            text = str(msg.get("text") or "")
            if not text:
                continue
            if kind in ("scene", "action", "narration"):
                self.add_bubble("narration", text, ts=ts)
            elif kind == "line":
                self.add_bubble("assistant", text, ts=ts)
            elif kind in ("user", "assistant"):
                self.add_bubble(kind, text, ts=ts)
        if group is not None:
            group.collapse()
        from PySide6.QtCore import QTimer
        for delay in (0, 50, 200):
            QTimer.singleShot(delay, self._settle)
        self.scroll_to_bottom()

    def load_history(self):
        """启动时把 session 回放出来，切页不会丢对话。"""
        try:
            data = api_get("/history?limit=40")
        except Exception:                    # noqa: BLE001
            return
        rows = data.get("messages") or []
        self._seen = int(data.get("total") or len(rows))
        group = None
        for row in rows:
            role = row.get("role")
            text = str(row.get("text") or "")
            if not text:
                continue
            if role == "tip":
                if group is None:
                    group = TipGroup()
                    self.messages.insertWidget(self.messages.count() - 1, group)
                group.add(text)
                continue
            group = None
            ts = row.get("ts") or ""
            if role in ("narration", "scene"):
                self.add_bubble("narration", text, ts=ts)
            elif role == "line":
                self.add_bubble("assistant", text, ts=ts)
            elif role in ("user", "assistant"):
                self.add_bubble(role, text, ts=ts)
        if group is not None:
            group.collapse()
        from PySide6.QtCore import QTimer
        for delay in (0, 50, 200):
            QTimer.singleShot(delay, self._settle)

    # ---------- 左侧状态栏 ----------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.panel.setGeometry(0, 0, 230, self.height())
        if hasattr(self, "lock_overlay") and hasattr(self, "input_shell"):
            self.lock_overlay.setGeometry(self.input_shell.rect())
        self._fit_all()

    def _poll_history(self):
        """她自己在后台醒过来时，把新消息补进界面。"""
        self.refresh_state()
        if self.worker and self.worker.isRunning():
            return
        try:
            data = api_get("/history?limit=80")
        except Exception:                    # noqa: BLE001
            return
        rows = data.get("messages") or []
        total = int(data.get("total") or len(rows))
        if total == 0:
            self._clear_bubbles()
            return
        if self._seen == 0:
            self._seen = total
            return
        if total < self._seen:              # 会话被清空或回滚：重画
            self._clear_bubbles()
            self._seen = 0
            return
        if total == self._seen:
            return
        fresh = rows[-(total - self._seen):]
        for row in fresh:
            role = row.get("role")
            text = str(row.get("text") or "")
            if not text:
                continue
            if role in ("narration", "scene"):
                self.add_bubble("narration", text, ts=row.get("ts") or "")
            elif role == "line":
                self.add_bubble("assistant", text, ts=row.get("ts") or "")
            elif role == "tip":
                self.add_bubble("tip", text)
            elif role in ("user", "assistant"):
                self.add_bubble(role, text, ts=row.get("ts") or "")
        self._seen = total
        self.scroll_to_bottom()

    def _tick_clock(self):
        """本地只负责显示后端给的最近计时器；到零请求 /sync。"""
        import time as _t
        if self._view_timer_id:
            left = self._timer_deadline - _t.monotonic()
            if left <= 0:
                self.header.clock.setText("◷ 00:00")
                self.header.clock.setToolTip(f"{self._view_timer_name or '计时器'}\n已到点")
                if not self._sync_pending:
                    self._sync_timer()
                return
            sec = int(left)
            if sec >= 3600:
                shown = f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
            else:
                shown = f"{sec // 60:02d}:{sec % 60:02d}"
            self.header.clock.setText(f"◷ {shown}")
            self.header.clock.setToolTip(f"{self._view_timer_name or '计时器'}\n剩余 {shown}")
            return
        self.header.clock.setText("◷ --:--")
        self.header.clock.setToolTip("当前没有计时器")

    def toggle_panel(self):
        show = not self.panel.isVisible()
        if show:
            self.panel.setGeometry(0, 0, 230, self.height())
            self.panel.show()
            self.panel.raise_()
            self.refresh_state()
        else:
            self.panel.hide()
        if hasattr(self, "state_btn"):
            self.state_btn.setChecked(show)

    # ---------- 模型 / 思考强度 ----------

    def _toggle_max(self):
        win = self.window()
        if win.isMaximized():
            win.showNormal()
            self._win_max.setText("□")
        else:
            win.showMaximized()
            self._win_max.setText("❐")

    def refresh_model_text(self):
        self.model_btn.setText(f"{self.model_name}　▾")
        self.think_btn.setText(f"{self.think_level.upper()}　▾")

    def open_picker(self):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(PICKER_QSS)
        head = menu.addAction("DeepSeek 账号")
        head.setEnabled(False)
        for name in ("DeepSeek-V4.1-Flash", "DeepSeek-V4-Pro"):
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == self.model_name)
            act.triggered.connect(lambda _=False, n=name: self.pick_model(n))
        menu.exec(self.model_btn.mapToGlobal(self.model_btn.rect().topLeft()))

    def open_think_picker(self):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(PICKER_QSS)
        head = menu.addAction("思考强度")
        head.setEnabled(False)
        for level in ("Off", "Low", "High", "Max"):
            act = menu.addAction(level)
            act.setCheckable(True)
            act.setChecked(level == self.think_level)
            act.triggered.connect(lambda _=False, lv=level: self.pick_think(lv))
        menu.exec(self.think_btn.mapToGlobal(self.think_btn.rect().topLeft()))

    def pick_model(self, name):
        self.model_name = name
        self.refresh_model_text()
        self._push_model()

    def pick_think(self, level):
        self.think_level = level
        self.refresh_model_text()
        self._push_model()

    def _push_model(self):
        model = "deepseek-flash" if "Flash" in self.model_name else "deepseek-v4-pro"
        effort = {"Off": "none", "Low": "low", "High": "high",
                  "Max": "max"}.get(self.think_level, "high")
        try:
            api_send("POST", "/config",
                     {"payload": {"model": model, "reasoning_effort": effort}})
        except Exception:                    # noqa: BLE001
            pass

    # ---------- 对话 ----------

    def refresh_state(self):
        if not hasattr(self, "panel"):
            return
        self.agent_name = "无名者"
        self.user_name = "无名者"
        try:
            s = api_get("/state")
        except Exception as e:               # noqa: BLE001
            self.panel.update_text(f"读不到状态：{e}")
            return
        self._last_message_at = s.get("lastMessageAt") or self._last_message_at
        self._timer_targets = []
        for t in s.get("timers") or []:
            try:
                at = float(t.get("at") or 0)
            except (TypeError, ValueError):
                continue
            if at:
                self._timer_targets.append((at, "动作", str(t.get("text") or "动作")))
        try:
            cfg = api_get("/config") or {}
            configured = str(((cfg.get("harness") or {}).get("name") or "")).strip()
            if configured:
                self.agent_name = configured
            configured_user = str(((cfg.get("payload") or {}).get("user_id") or "")).strip()
            if configured_user:
                self.user_name = configured_user
            self._update_bubble_names()
            node = ((cfg.get("harness") or {}).get("idle")) or {}
            self._idle_enabled = bool(node.get("enabled", True))
            self._idle_minutes = max(1.0, float(node.get("afterMinutes", 30)))
        except Exception:                    # noqa: BLE001
            pass
        self._silence_at = 0
        if (self._idle_enabled and s.get("active") != "sleep"
                and self._last_message_at):
            self._silence_at = float(self._last_message_at) + self._idle_minutes * 60
        pos = (s.get("local") or "未知").split("/")[-1]
        state_txt = "睡着" if s.get("active") == "sleep" else "醒着"
        loc = s.get("local") or "未知"
        self.info.setText(f"角色位置：{loc}\n状态：{state_txt}")
        self.info.setToolTip(f"角色位置：{loc}\n状态：{state_txt}")
        m = s.get("metabolism") or {}
        lines = [f"位置　{s.get('local') or '未知'}",
                 f"状态　{'睡着' if s.get('active') == 'sleep' else '醒着'}",
                 "",
                 f"饱食　{m.get('satiety', 0):.0f}",
                 f"水分　{m.get('hydration', 0):.0f}",
                 f"疲劳　{m.get('fatigue', 0):.0f}",
                 f"困意　{m.get('sleepiness', 0):.0f}"]
        for t in s.get("timers") or []:
            lines.append(f"计时　{t.get('text') or ''}")
        self.panel.update_text("\n".join(lines))
        pending = s.get("pending") or []
        self.pending_label.setText(f"{len(pending)} 件事刚发生" if pending else "")

    def _chat_menu(self, pos):
        """消息区的右键菜单。"""
        menu = QMenu(self)
        menu.setStyleSheet(PICKER_QSS)
        menu.addAction("刷新", self.reload_all)
        menu.exec(self.scroll.viewport().mapToGlobal(pos))

    def _clear_bubbles(self):
        """把消息区清空（会话被重置时用）。"""
        while self.messages.count() > 1:
            item = self.messages.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._seen = 0

    def reload_all(self):
        """手动刷新：重新拉完整 view。"""
        self.refresh_view()

    def add_bubble(self, role, text, media=None, ts="", split=None):
        if not ts:
            import time as _t
            ts = _t.strftime("%Y-%m-%d %H:%M:%S")
        parts = [text]
        if not parts:
            parts = [""]
        for index, part in enumerate(parts):
            bubble = Bubble(role, part, media if index == 0 else None,
                            ts if index == 0 else "",
                            split=SPLIT_MESSAGES if split is None else split,
                            agent_name=getattr(self, "agent_name", "无名者"),
                            user_name=getattr(self, "user_name", "无名者"))
            self.messages.insertWidget(self.messages.count() - 1, bubble)
            self.messages.setAlignment(bubble, Qt.AlignRight if role == "user" else Qt.AlignLeft)
            self._fit_width(bubble)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _refresh_identity(self):
        try:
            cfg = api_get("/config") or {}
        except Exception:                    # noqa: BLE001
            return
        agent = str(((cfg.get("harness") or {}).get("name") or "")).strip() or "无名者"
        user = str(((cfg.get("payload") or {}).get("user_id") or "")).strip() or "无名者"
        if agent != self.agent_name or user != self.user_name:
            self.agent_name = agent
            self.user_name = user
            self._update_bubble_names()

    def _update_bubble_names(self):
        for i in range(self.messages.count()):
            item = self.messages.itemAt(i).widget()
            if not isinstance(item, Bubble):
                continue
            if item.role == "user":
                item.name_label.setText(self.user_name or "无名者")
            elif item.role == "assistant":
                item.name_label.setText(self.agent_name or "无名者")

    def set_locked(self, locked, reason="运行中"):
        self._locked = bool(locked)
        self.input.setEnabled(not self._locked)
        self.send_btn.setEnabled(not self._locked)
        if hasattr(self, "lock_overlay"):
            self.lock_overlay.setGeometry(self.input_shell.rect())
            self.lock_overlay.setVisible(self._locked)
            if self._locked:
                self.lock_overlay.raise_()
        self.input.setPlaceholderText("正在运行…" if self._locked else "说点什么…")

    def send(self):
        if getattr(self, "_locked", False):
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._follow = True
        # 乐观显示；后端 view 到达前由 _pending_user 兜住
        self._pending_user = {"text": text}
        self.add_bubble("user", text)
        self.scroll_to_bottom()
        try:
            data = api_send("POST", "/turn", {"text": text})
        except Exception as exc:              # noqa: BLE001
            self._pending_user = None
            self.add_bubble("narration", f"[发送失败] {type(exc).__name__}: {exc}")
            return
        if isinstance(data, dict) and data.get("error"):
            self._pending_user = None
            self.add_bubble("narration", f"[发送失败] {data['error']}")

    def _fit_width(self, bubble):
        if getattr(bubble, "role", "") == "narration":
            limit = int(self.scroll.viewport().width() * 0.9)
            labels = bubble.findChildren(QLabel)
            if not labels:
                return
            label = labels[-1]
            fm = QFontMetrics(label.font())
            natural = fm.horizontalAdvance(label.text()) + 44
            label.setFixedWidth(min(limit, max(220, natural)))
            return
        bodies = getattr(bubble, "bodies", None)
        if not bodies:
            return
        limit = int(self.scroll.viewport().width() * 0.9)
        widest = 160
        for body in bodies:
            body.ensurePolished()
            fm = QFontMetrics(body.font())
            raw = body.property("raw") or body.text()
            body.setText(wrap_to_px(raw, fm, limit - 54))
            longest = max((fm.horizontalAdvance(line)
                           for line in body.text().splitlines()), default=0)
            width = min(limit, max(160, int(longest * 1.06) + 54))
            body.setFixedWidth(width)
            widest = max(widest, width)
        bubble.setFixedWidth(widest + 4)

    def _fit_all(self):
        for i in range(self.messages.count()):
            item = self.messages.itemAt(i).widget()
            if isinstance(item, Bubble):
                self._fit_width(item)

    def _settle(self):
        self._fit_all()
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_range(self, _minimum, _maximum):
        if self._follow:
            self._vbar.setValue(self._vbar.maximum())

    def _on_scroll(self, value):
        self._follow = value >= self._vbar.maximum() - 8

    def scroll_to_bottom(self):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))

    def _on_usage(self, data):
        def num(value):
            value = int(value or 0)
            if value >= 1_000_000:
                return f"{value / 1_000_000:.3f}M"
            if value >= 10_000:
                return f"{value / 1000:.1f}k"
            return str(value)

        self.usage.setText(
            f"输入 {num(data.get('prompt'))}   输出 {num(data.get('completion'))}   "
            f"平均命中 {data.get('avg', 0):.0f}%   单次命中 {data.get('cache', 0):.0f}%   "
            f"已用上下文 {data.get('ratio', 0):.1f}%")

    def _above_stream(self, role, text):
        """把一条气泡插在流式正文上方（按到达顺序向上堆叠）。"""
        import time as _t
        bubble = Bubble(role, text, None, _t.strftime("%Y-%m-%d %H:%M:%S"))
        stream = getattr(self, "_stream_bubble", None)
        at = self.messages.indexOf(stream) if stream is not None else -1
        if at < 0:
            at = self.messages.count() - 1
        self.messages.insertWidget(at, bubble)
        self._fit_width(bubble)
        if getattr(self, "_follow", True):
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        return bubble

    def _on_tool(self, payload):
        kind = str(payload.get("kind") or "")
        text = str(payload.get("text") or "")
        if not text:
            return
        if kind == "scene":
            self._above_stream("narration", text)
            return
        if kind == "action":
            self._above_stream("narration", text)
            return
        if kind == "line":
            self._above_stream("assistant", text)
            return
        if kind == "narration":
            self._above_stream("narration", text)
            return
        if self._tip_group is None:
            self._tip_group = TipGroup()
            self.messages.insertWidget(self.messages.count() - 1, self._tip_group)
        self._tip_group.add(text)

    def _on_delta(self, text):
        self._buffer = getattr(self, "_buffer", "") + text
        label = getattr(self, "_stream_label", None)
        bubble = getattr(self, "_stream_bubble", None)
        if label is not None:
            label.setText(wrap_chars(self._buffer))
            if bubble is not None:
                self._fit_width(bubble)
                if self._follow:
                    self._vbar.setValue(self._vbar.maximum())
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_done(self, text):
        label = getattr(self, "_stream_label", None)
        if label is not None and text and text != getattr(self, "_buffer", ""):
            label.setText(wrap_chars(text))
        if self._tip_group is not None:
            self._tip_group.collapse()
            self._tip_group = None
        self.refresh_view()

    def _on_error(self, message):
        label = getattr(self, "_stream_label", None)
        if label is not None:
            label.setText(f"[连接出错] {message}")


# ---------------- 配置页 ----------------

VISIBLE = {
    "payload.api", "payload.max_tokens", "payload.stream",
    "payload.temperature", "payload.user_id",
    "harness.name", "harness.usage.maxTurn",
    "harness.idle.enabled", "harness.idle.afterMinutes",
    "harness.ui.splitMessages",
    "harness.retrieval.queryTurns.withMessage",
    "harness.retrieval.queryTurns.maxChars",
    "harness.retrieval.memory.topK",
    "harness.retrieval.memory.blockTopK",
    "harness.retrieval.memory.sliceFloor",
    "harness.retrieval.memory.sliceMargin",
    "harness.retrieval.memory.blockFloor",
    "harness.retrieval.lorebook.maxItems",
    "harness.retrieval.lorebook.budgetChars",
    "harness.dream.block.minRecalledChars",
    "harness.dream.block.minRecalls",
    "harness.dream.memory.minRecalls",
    "harness.dream.memory.minSources",
    "harness.dream.persona.minEvents",
    "harness.dream.maxTokens",
    "tools.tavily.api",
}


SPLIT_MESSAGES = True          # 是否把一条消息按行拆成多个气泡，由配置决定


def refresh_ui_settings():
    """从服务端读一次界面相关的开关。"""
    global SPLIT_MESSAGES
    try:
        cfg = api_get("/config")
        SPLIT_MESSAGES = bool(((cfg.get("harness") or {}).get("ui") or {})
                              .get("splitMessages", True))
    except Exception:                    # noqa: BLE001
        pass


def visible(path):
    return ".".join(path) in VISIBLE


def has_visible(node, path):
    """子树里有没有白名单字段，没有就整组隐藏。"""
    for key, value in node.items():
        if isinstance(value, dict):
            if has_visible(value, path + (key,)):
                return True
        elif visible(path + (key,)):
            return True
    return False


LABELS = {
    "payload.model": "模型",
    "payload.thinking.type": "思考开关",
    "payload.reasoning_effort": "思考强度",
    "payload.max_tokens": "输出上限",
    "payload.stream": "默认流式",
    "payload.api": "接口密钥",
    "payload.stream_options.include_usage": "统计用量",
    "payload.temperature": "温度",
    "payload.top_p": "采样范围",
    "payload.tool_choice": "工具选择",
    "payload.logprobs": "返回概率",
    "payload.top_logprobs": "概率条数",
    "payload.user_id": "用户标识",
    "harness.name": "角色名",
    "harness.idle.enabled": "静默唤醒",
    "harness.idle.afterMinutes": "静默多久醒来（分钟）",
    "harness.ui.splitMessages": "切分消息条",
    "harness.usage.maxTurn": "统计轮数",
    "harness.usage.avgWindow": "平均命中窗口",
    "harness.session.compressionThreshold": "压缩阈值",
    "harness.session.retainedTrun": "保留轮次",
    "harness.subprocess.timeout": "工具超时",
    "harness.loop.maxRounds": "循环上限",
    "harness.loop.maxToolOutput": "工具输出上限",
    "harness.retrieval.queryTurns.withMessage": "检索轮次（对话）",
    "harness.retrieval.queryTurns.loopRound": "检索轮次（循环）",
    "harness.retrieval.queryTurns.maxChars": "检索字数上限",
    "harness.retrieval.memory.topK": "记忆召回条数",
    "harness.retrieval.memory.blockTopK": "记忆块条数",
    "harness.retrieval.memory.sliceFloor": "切片阈值",
    "harness.retrieval.memory.sliceMargin": "切片相对余量",
    "harness.retrieval.memory.blockFloor": "块阈值",
    "harness.retrieval.lorebook.maxItems": "世界书条数",
    "harness.retrieval.lorebook.budgetChars": "世界书字数预算",
    "harness.plan.stepWindow": "计划步长窗口",
    "harness.plan.activeLimit": "进行中上限",
    "harness.plan.pausedLimit": "暂停上限",
    "harness.network.verifySSL": "校验证书",
    "harness.debug.dump": "调试快照",
    "harness.debug.dumpTail": "快照尾部",
    "harness.dream.block.minRecalledChars": "沉淀字符下限",
    "harness.dream.block.minRecalls": "沉淀召回次数",
    "harness.dream.memory.minRecalls": "记忆召回次数",
    "harness.dream.memory.minSources": "证据数量要求",
    "harness.dream.persona.minEvents": "证据数量要求",
    "harness.dream.maxTokens": "升格输出上限",
    "tools.tavily.api": "搜索密钥",
}

GROUPS = {
    "payload": "模型请求",
    "payload.thinking": "思考",
    "payload.stream_options": "流式选项",
    "harness": "运行参数",
    "harness.usage": "用量",
    "harness.idle": "静默唤醒",
    "harness.session": "会话与压缩",
    "harness.subprocess": "子进程",
    "harness.loop": "工具循环",
    "harness.retrieval": "向量召回",
    "harness.retrieval.queryTurns": "检索轮次",
    "harness.retrieval.memory": "记忆",
    "harness.retrieval.lorebook": "世界书",
    "harness.plan": "计划",
    "harness.network": "网络",
    "harness.debug": "调试",
    "harness.dream": "人格维护",
    "harness.dream.block": "切片 → 记忆向量",
    "harness.dream.memory": "记忆向量 → 常驻记忆",
    "harness.dream.persona": "常驻记忆 → 人格准则",
    "tools": "外部工具",
    "tools.tavily": "Tavily 搜索",
}


def zh(path, table, fallback):
    key = ".".join(path)
    return table.get(key, fallback)


class ConfigPage(QWidget):
    """把 state/Philosophical.json 整棵树铺成表单。"""

    def __init__(self):
        super().__init__()
        self.editors = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 16, 28, 14)
        outer.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("设置")
        title.setObjectName("h1")
        head.addWidget(title)
        head.addStretch(1)
        self.hint = QLabel("")
        self.hint.setObjectName("info")
        head.addWidget(self.hint)
        save = QPushButton("保存")
        save.clicked.connect(self.save)
        head.addWidget(save)
        outer.addLayout(head)
        self.actions_box = QGroupBox("手动任务")
        actions_layout = QVBoxLayout(self.actions_box)
        actions_layout.setContentsMargins(12, 8, 12, 10)
        actions_layout.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.action_buttons = {}
        self.action_hint = QLabel("")
        self.action_hint.setObjectName("info")
        self.action_hint.setWordWrap(True)
        actions_layout.addLayout(row)
        actions_layout.addWidget(self.action_hint)
        self.action_timer = QTimer(self)
        self.action_timer.setInterval(1500)
        self.action_timer.timeout.connect(self.poll_actions)
        outer.addWidget(self.actions_box)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.holder = QWidget()
        self.form = QVBoxLayout(self.holder)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setSpacing(12)
        self.form.addStretch(1)
        self.scroll.setWidget(self.holder)
        outer.addWidget(self.scroll, 1)

        self.load()
        self.build_actions()

    # ---- 手动任务 ----

    def build_actions(self):
        try:
            info = api_get("/action")
        except Exception:                    # noqa: BLE001
            self.actions_box.setVisible(False)
            return
        row = self.action_buttons["_row"] if "_row" in self.action_buttons else None
        layout = self.actions_box.layout().itemAt(0).layout()
        for name, label in (info.get("available") or {}).items():
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, n=name: self.run_action(n))
            layout.addWidget(button)
            self.action_buttons[name] = button
        layout.addStretch(1)

    def run_action(self, name):
        try:
            result = api_send("POST", "/action", {"action": name})
        except Exception as error:            # noqa: BLE001
            self.action_hint.setText(f"启动失败：{error}")
            return
        if not result.get("ok"):
            self.action_hint.setText(f"没启动：{result.get('detail')}")
            return
        self.action_hint.setText(f"「{name}」已在后台运行…")
        self.action_timer.start()

    def poll_actions(self):
        try:
            info = api_get("/action")
        except Exception:                    # noqa: BLE001
            self.action_timer.stop()
            return
        running, lines = [], []
        for name, job in (info.get("jobs") or {}).items():
            if job.get("running"):
                running.append(name)
                continue
            result = job.get("result") or {}
            if result:
                mark = "完成" if result.get("ok") else "失败"
                lines.append(f"{name} {mark}：{result.get('detail', '')}"
                             f"（{result.get('ms', 0)}ms）")
        if running:
            self.action_hint.setText("正在运行：" + "、".join(running))
        else:
            self.action_timer.stop()
            self.action_hint.setText("\n".join(lines[-3:]) or "没有任务记录")

    # ---- 构建 ----

    def load(self):
        try:
            cfg = api_get("/config")
        except Exception as error:            # noqa: BLE001
            self.hint.setText(f"读取失败：{error}")
            return
        self.editors.clear()
        while self.form.count() > 1:
            item = self.form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for section, values in cfg.items():
            if not isinstance(values, dict) or not has_visible(values, (section,)):
                continue
            self.form.insertWidget(self.form.count() - 1, self._section(section, values))
        self.hint.setText("来自 state/Philosophical.json")

    def _section(self, name, values):
        card = QFrame()
        card.setObjectName("card")
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(6)

        title = QLabel(name)
        title.setObjectName("h1")
        box.addWidget(title)
        box.addSpacing(4)
        self._rows(box, values, (name,))
        return card

    def _rows(self, box, values, path, depth=0):
        for key, value in values.items():
            if key in ("messages", "tools"):
                continue

            if isinstance(value, dict):
                if not has_visible(value, path + (key,)):
                    continue
                group = QWidget()
                group_layout = QVBoxLayout(group)
                group_layout.setContentsMargins(14 * depth, 10 if depth else 14, 0, 0)
                group_layout.setSpacing(4)

                head = QHBoxLayout()
                head.setContentsMargins(0, 0, 0, 0)
                marker = QFrame()
                marker.setFixedWidth(2)
                marker.setStyleSheet("background: #d8d5cf;")
                head.addWidget(marker)
                head.addSpacing(8)
                name = QLabel(zh(path + (key,), GROUPS, key))
                name.setObjectName("group")
                head.addWidget(name)
                head.addStretch(1)
                group_layout.addLayout(head)
                group_layout.addSpacing(2)

                self._rows(group_layout, value, path + (key,), depth + 1)
                box.addWidget(group)
                box.addSpacing(6)
                continue

            if not visible(path + (key,)):
                continue
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(14 * depth + 10, 0, 0, 0)
            row_layout.setSpacing(10)

            name = QLabel(zh(path + (key,), LABELS, key))
            name.setFixedWidth(180)
            name.setObjectName("fieldName")
            row_layout.addWidget(name)

            if isinstance(value, bool):
                editor = QCheckBox()
                editor.setChecked(value)
            else:
                editor = QLineEdit(str(value))
            editor.setMaximumWidth(520)
            row_layout.addWidget(editor, 1)
            row_layout.addStretch(1)
            box.addWidget(row)
            box.addSpacing(3)
            self.editors[path + (key,)] = (editor, type(value))

    # ---- 保存 ----

    def save(self):
        config = {}
        for path, (editor, kind) in self.editors.items():
            if kind is bool:
                value = editor.isChecked()
            elif kind is int:
                try:
                    value = int(editor.text().strip())
                except ValueError:
                    continue
            elif kind is float:
                try:
                    value = float(editor.text().strip())
                except ValueError:
                    continue
            else:
                value = editor.text().strip()

            node = config
            for part in path[:-1]:
                node = node.setdefault(part, {})
            node[path[-1]] = value

        turns = (config.get("harness") or {}).get("retrieval", {}).get("queryTurns")
        if isinstance(turns, dict) and isinstance(turns.get("withMessage"), int):
            turns["loopRound"] = turns["withMessage"] + 1      # 循环轮次 = 对话轮次 + 1

        try:
            api_send("POST", "/config", {"config": config})
            refresh_ui_settings()
            # 界面开关变了，已有的消息按新设置重画一遍
            try:
                pages = getattr(self.window(), "pages", None)
                chat = pages.findChild(ChatPage) if pages is not None else None
                if chat is not None:
                    chat.reload_all()
            except Exception:            # noqa: BLE001
                pass
            self.hint.setText("已保存")
        except Exception as error:            # noqa: BLE001
            self.hint.setText(f"保存失败：{error}")


# ---------------- 识域页 ----------------

class SkillPage(QWidget):
    """技能：左列标题，右侧关键词、关键词池与正文。"""

    def __init__(self):
        super().__init__()
        self.items = []
        self.current = -1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 16, 28, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("技能")
        title.setObjectName("h1")
        head.addWidget(title)
        head.addStretch(1)
        self.constant = QCheckBox("是否常驻")
        head.addWidget(self.constant)
        self.hint = QLabel("")
        self.hint.setObjectName("info")
        head.addWidget(self.hint)
        save = QPushButton("保存")
        save.clicked.connect(self.save)
        head.addWidget(save)
        layout.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.list = QListWidget()
        self.list.setFixedWidth(190)
        self.list.currentRowChanged.connect(self.select)
        body.addWidget(self.list)

        right = QVBoxLayout()
        right.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._label("关键词池"))
        self.pool = QLineEdit()
        self.pool.setPlaceholderText("用、分隔，例如：打铁、烛火、莱塔尼亚")
        row.addWidget(self.pool, 1)
        right.addLayout(row)

        self.body = QPlainTextEdit()
        self.body.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        right.addWidget(self.body, 1)
        body.addLayout(right, 1)
        layout.addLayout(body, 1)

        self.load()

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setObjectName("info")
        return label

    def load(self):
        data = api_get("/skill") or {}
        self.items = data.get("items") or []
        self.list.clear()
        for index, item in enumerate(self.items, 1):
            self.list.addItem(str(item.get("title") or f"技能 {index}"))
        if self.list.count():
            self.list.setCurrentRow(0)
        self.hint.setText(f"{len(self.items)} 条")

    def select(self, index):
        if not (0 <= index < len(self.items)):
            return
        self.current = index
        item = self.items[index]
        self.constant.setChecked(bool(item.get("constant")))
        self.pool.setText("、".join(str(k) for k in item.get("keywords") or []))
        self.body.setPlainText(str(item.get("content") or ""))

    def save(self):
        if not (0 <= self.current < len(self.items)):
            return
        item = self.items[self.current]
        item["constant"] = self.constant.isChecked()
        item["keywords"] = [k.strip() for k in
                            self.pool.text().replace("，", "、").split("、") if k.strip()]
        item["content"] = self.body.toPlainText().strip()
        result = api_send("POST", "/skill", {"items": self.items})
        self.hint.setText(f"已保存 {result.get('count', 0)} 条")


class RealmPage(QWidget):
    """识域：左侧文件列表，右侧原文；末尾追加「技能」。"""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 16, 28, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("识域")
        title.setObjectName("h1")
        head.addWidget(title)
        head.addStretch(1)
        self.hint = QLabel("")
        self.hint.setObjectName("info")
        head.addWidget(self.hint)
        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self.save_file)
        head.addWidget(self.save_btn)
        layout.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)

        self.list = QListWidget()
        self.list.setFixedWidth(190)
        self.list.currentTextChanged.connect(self.load_entry)
        body.addWidget(self.list)

        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.editor.setObjectName("realmEditor")
        body.addWidget(self.editor, 1)
        layout.addLayout(body, 1)

        self.load_list()

    def load_list(self):
        try:
            files = api_get("/realm").get("files") or []
        except Exception:                    # noqa: BLE001
            files = []
        self.list.clear()
        self.list.addItems(files)
        if self.list.count():
            self.list.setCurrentRow(0)

    def load_entry(self, name):
        if not name:
            return
        try:
            data = api_get(f"/realm/{name}")
        except Exception as error:            # noqa: BLE001
            self.hint.setText(f"读取失败：{error}")
            return
        self.editor.setReadOnly(False)
        self.save_btn.setVisible(True)
        self.editor.setPlainText(data.get("text") or "")
        self.hint.setText(f"{name} · {len(data.get('text') or '')} 字")

    def save_file(self):
        name = self.list.currentItem()
        if not name:
            return
        result = api_send("PUT", f"/realm/{name.text()}",
                          {"text": self.editor.toPlainText()})
        self.hint.setText(f"已保存 {result.get('chars', 0)} 字")


# ---------------- 主窗口 ----------------

class NavButton(QWidget):
    """64px 侧栏图标按钮：透明底、无边框，只有图标和极小的名字。"""

    clicked = Signal()

    def __init__(self, key, label, parent=None):
        super().__init__(parent)
        self.key = key
        self.label = label
        self._active = False
        self._hover = False
        self.setFixedSize(64, 70)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(label)
        self.setAttribute(Qt.WA_Hover, True)

    def set_active(self, on):
        if self._active != bool(on):
            self._active = bool(on)
            self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor("#f7e7c4") if self._active else QColor(
            "#e4d3b6" if self._hover else "#c8b89f")
        if self._active:
            painter.setPen(QPen(QColor("#e7c58a"), 2))
            painter.drawLine(1, 16, 1, self.height() - 16)
        self._icon(painter, self.key, self.width() // 2, 25, 10.0, color)
        painter.setPen(QPen(color, 1))
        font = painter.font()
        font.setPointSizeF(8.2)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, 48, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                         self.label)

    @staticmethod
    def _icon(painter, key, cx, cy, size, color):
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if key == "chat":
            path = QPainterPath()
            path.addRoundedRect(cx - size, cy - size * 0.72, size * 2, size * 1.35, 4, 4)
            painter.drawPath(path)
            painter.drawLine(cx - size * 0.55, cy - size * 0.05,
                             cx + size * 0.55, cy - size * 0.05)
            painter.drawLine(cx - size * 0.55, cy + size * 0.35,
                             cx + size * 0.15, cy + size * 0.35)
            tail = QPainterPath()
            tail.moveTo(cx - size * 0.55, cy + size * 0.62)
            tail.lineTo(cx - size * 0.80, cy + size * 1.05)
            tail.lineTo(cx - size * 0.20, cy + size * 0.62)
            painter.drawPath(tail)
        elif key == "comm":
            painter.drawEllipse(cx - 3, cy - 3, 6, 6)
            for r in (size * 0.65, size * 1.05):
                painter.drawArc(cx - r, cy - r, r * 2, r * 2, 40 * 16, 100 * 16)
                painter.drawArc(cx - r, cy - r, r * 2, r * 2, 220 * 16, 100 * 16)
        elif key == "realm":
            left = QPainterPath()
            left.moveTo(cx, cy - size * 0.55)
            left.cubicTo(cx - size * 0.25, cy - size * 0.85, cx - size * 0.85, cy - size * 0.75,
                         cx - size, cy - size * 0.45)
            left.lineTo(cx - size, cy + size * 0.55)
            left.cubicTo(cx - size * 0.65, cy + size * 0.30, cx - size * 0.25, cy + size * 0.35,
                         cx, cy + size * 0.62)
            painter.drawPath(left)
            right = QPainterPath()
            right.moveTo(cx, cy - size * 0.55)
            right.cubicTo(cx + size * 0.25, cy - size * 0.85, cx + size * 0.85, cy - size * 0.75,
                          cx + size, cy - size * 0.45)
            right.lineTo(cx + size, cy + size * 0.55)
            right.cubicTo(cx + size * 0.65, cy + size * 0.30, cx + size * 0.25, cy + size * 0.35,
                          cx, cy + size * 0.62)
            painter.drawPath(right)
            painter.drawLine(cx, cy - size * 0.55, cx, cy + size * 0.62)
        elif key == "skill":
            path = QPainterPath()
            for i in range(8):
                import math
                r = size * 1.05 if i % 2 == 0 else size * 0.32
                a = math.radians(-90 + i * 45)
                x, y = cx + r * math.cos(a), cy + r * math.sin(a)
                path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawEllipse(cx - 1.5, cy - 1.5, 3, 3)
        elif key == "settings":
            for offset, knob in ((-size * 0.62, -size * 0.25), (0, size * 0.32),
                                 (size * 0.62, -size * 0.05)):
                y = cy + offset
                painter.drawLine(cx - size, y, cx + size, y)
                painter.drawEllipse(cx + knob - 2, y - 2, 4, 4)

class CommPage(QWidget):
    """通信设备：占位页，保持与主会话相同的透明容器风格。"""

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 18, 28, 14)
        shell = QFrame()
        shell.setObjectName("chatShell")
        box = QVBoxLayout(shell)
        box.setContentsMargins(24, 20, 24, 20)
        title = QLabel("通信设备")
        title.setObjectName("h1")
        box.addWidget(title)
        hint = QLabel("尚未接入通信设备。")
        hint.setObjectName("muted")
        box.addWidget(hint)
        box.addStretch(1)
        outer.addWidget(shell, 1)

class WindowControls(QWidget):
    """无边框窗口的最小化/最大化/关闭，固定在右下角。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(92, 26)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.btn_min = QPushButton("−")
        self.btn_max = QPushButton("□")
        self.btn_close = QPushButton("×")
        for btn, tip in ((self.btn_min, "最小化"), (self.btn_max, "最大化"),
                         (self.btn_close, "关闭")):
            btn.setObjectName("winBtn")
            btn.setFixedSize(28, 24)
            btn.setFlat(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            row.addWidget(btn)
        self.btn_close.setObjectName("winClose")
        self.btn_min.clicked.connect(lambda: self.window().showMinimized())
        self.btn_max.clicked.connect(self.toggle_max)
        self.btn_close.clicked.connect(lambda: self.window().close())

    def toggle_max(self):
        win = self.window()
        if win.isMaximized():
            win.showNormal()
            self.btn_max.setText("□")
        else:
            win.showMaximized()
            self.btn_max.setText("❐")


class DragBar(QWidget):
    """无边框窗口的透明拖动带，只占顶部 10px，不挡胶囊。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self.setCursor(Qt.SizeAllCursor)
        self.setStyleSheet("background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        win = self.window()
        if win.isMaximized():
            win.showNormal()
        else:
            win.showMaximized()
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        refresh_ui_settings()
        self.setWindowTitle(f"PhilosophicalZombie · {BUILD}")
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        width = min(1440, max(1080, (avail.width() if avail else 1440) - 16))
        height = min(900, max(700, (avail.height() if avail else 900) - 16))
        self.setMinimumSize(1080, 700)
        self.resize(width, height)
        if avail is not None:
            self.move(avail.x() + (avail.width() - width) // 2,
                      avail.y() + (avail.height() - height) // 2)

        self.backdrop = Backdrop(self)
        self.backdrop.lower()
        self.dragbar = DragBar(self)
        self.dragbar.raise_()
        self.winbar = WindowControls(self)
        self.winbar.raise_()

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        nav = QWidget()
        nav.setFixedWidth(64)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 14, 0, 14)
        nav_layout.setSpacing(2)

        self.pages = QStackedWidget()
        self.buttons = []
        for index, (key, label, page) in enumerate([
                ("chat", "主会话", ChatPage()),
                ("comm", "通信设备", CommPage()),
                ("realm", "识域", RealmPage()),
                ("skill", "技能", SkillPage()),
                ("settings", "设置", ConfigPage())]):
            btn = NavButton(key, label)
            btn.set_active(index == 0)
            btn.clicked.connect(lambda i=index: self.switch(i))
            nav_layout.addWidget(btn)
            self.buttons.append(btn)
            self.pages.addWidget(page)
        nav_layout.addStretch(1)

        layout.addWidget(nav)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.setStyleSheet(QSS)
        self.position_winbar()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.backdrop.setGeometry(0, 0, self.width(), self.height())
        self.backdrop.lower()
        if hasattr(self, "dragbar"):
            self.dragbar.setGeometry(0, 0, self.width(), 10)
            self.dragbar.raise_()
        if hasattr(self, "winbar"):
            self.position_winbar()

    def position_winbar(self):
        if not hasattr(self, "winbar"):
            return
        page = self.pages.currentWidget()
        if isinstance(page, ChatPage):
            self.winbar.hide()
            return
        self.winbar.show()
        self.winbar.move(self.width() - 104, self.height() - 38)
        self.winbar.raise_()

    def switch(self, index):
        for i, btn in enumerate(self.buttons):
            btn.set_active(i == index)
        self.pages.setCurrentIndex(index)
        self.position_winbar()
        page = self.pages.currentWidget()
        if isinstance(page, ChatPage):
            page.refresh_view()

    def closeEvent(self, event):
        for i in range(self.pages.count()):
            page = self.pages.widget(i)
            if isinstance(page, ChatPage):
                page.shutdown()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    print(f"[UI] build={BUILD}  server={BASE}", flush=True)
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
