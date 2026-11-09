"""PhilosophicalZombie 桌面端骨架（PySide6）。

三页：对话 / 配置 / 识域。平面化视觉——灰白底、单色强调、细线分隔，
不用渐变和阴影。

    python ui/app.py

后端要先跑：python engine/server.py
"""

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSplitter,
                               QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

BASE = os.environ.get("PZ_SERVER", "http://127.0.0.1:8765")
ASSET_DIR = Path(__file__).resolve().parent / "assets"
BACKDROP_IMAGE = ASSET_DIR / "background.png"
BUILD = "b1106-1"
ACCENT = "#2f2f2f"
PICKER_QSS = """
QMenu { background: #ffffff; border: 1px solid #d8d5cf; padding: 6px; }
QMenu::item { padding: 7px 26px 7px 12px; }
QMenu::item:selected { background: #efece6; }
QMenu::item:disabled { color: #8a857c; }
QMenu::separator { height: 1px; background: #e6e3dd; margin: 6px 8px; }
"""
PAPER = "#f6f5f2"
LINE = "#d8d5cf"
MUTED = "#8a857c"

QSS = f"""
QMainWindow, QDialog {{ background: {PAPER}; }}
QWidget {{ background: transparent; color: #23211e; font-size: 13.5px; }}
QLabel#h1 {{ font-size: 19px; font-weight: 600; letter-spacing: 2px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#info {{ color: #a8a49c; font-size: 12px; }}
QLabel#narration {{ color: #FFC90E; background: rgba(35,33,30,0.58);
                    padding: 4px 12px; border-radius: 8px; }}
QLabel#group {{ color: #000000; font-size: 12.5px; font-weight: 600;
                letter-spacing: 1px; }}
QLabel#fieldName {{ color: #000000; }}
QLineEdit {{ color: #000000; }}
QLabel#tipItem {{ color: #b3aea6; }}
QLabel#stamp {{ color: #b3aea6; font-size: 10px; }}
QLabel#clock {{ color: #FFC90E; font-size: 12px; padding: 0 14px;
               background: rgba(35,33,30,0.55); border-radius: 9px; }}
QLabel#usage {{ color: #FFC90E; font-size: 12px;
               background: rgba(35,33,30,0.55); border-radius: 11px; }}
QFrame#card {{ background: rgba(255,255,255,0.72); border: 1px solid {LINE}; border-radius: 6px; }}
QFrame#bubbleUser {{ background: #ffffff; border: 1px solid {LINE};
                     border-top-right-radius: 10px; border-bottom-right-radius: 10px;
                     border-top-left-radius: 10px; border-bottom-left-radius: 3px; }}
QFrame#bubbleHer {{ background: #ffffff; border: 1px solid {LINE};
                    border-top-left-radius: 10px; border-bottom-left-radius: 10px;
                    border-top-right-radius: 10px; border-bottom-right-radius: 3px; }}
QPushButton {{ border: 1px solid {LINE}; background: #ffffff; padding: 6px 16px;
               border-radius: 5px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton#nav {{ border: none; border-radius: 0; text-align: left;
                   padding: 9px 18px; background: transparent; color: {MUTED}; }}
QPushButton#nav:checked {{ background: rgba(255,255,255,0.60); border-left: 2px solid {ACCENT};
                           color: #23211e; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox {{
    border: 1px solid {LINE}; background: rgba(255,255,255,0.62); padding: 7px 9px; border-radius: 5px; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #d8d5cf; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QListWidget {{ border: 1px solid {LINE}; background: #ffffff; border-radius: 6px; }}
QListWidget::item {{ padding: 8px 10px; }}
QListWidget::item:selected {{ background: #efece6; color: #23211e; }}
QPushButton#tipHeader {{ border: none; background: transparent; color: #a8a49c;
                         padding: 2px 4px; text-align: left; font-size: 11px; }}
QPushButton#tipHeader:hover {{ color: {ACCENT}; background: #efece6; }}
QPushButton#picker {{ border: 1px solid {LINE}; padding: 5px 14px; background: rgba(255,255,255,0.62);
                      border-radius: 5px; color: {MUTED}; }}
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
        """背景图铺满窗口，压到很淡，当作纸面底画。"""
        if self.picture.isNull():
            return
        scaled = self.picture.scaled(self.size(), Qt.KeepAspectRatioByExpanding,
                                     Qt.SmoothTransformation)
        painter.save()
        painter.setOpacity(0.64)
        painter.drawPixmap((self.width() - scaled.width()) // 2,
                           (self.height() - scaled.height()) // 2, scaled)
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(PAPER))
        self._draw_picture(painter)

        painter.setPen(QPen(self.INK, 1.15))
        painter.setBrush(Qt.NoBrush)

        width, height = self.width(), self.height()
        # 左边：两串，长短错开
        self._hang(painter, 26, height * 0.52, 13, leaves=6)
        self._hang(painter, 58, height * 0.30, -9, leaves=4)
        # 右边：镜像，位置更靠上一点
        self._hang(painter, width - 26, height * 0.44, -13, leaves=5)
        self._hang(painter, width - 60, height * 0.24, 8, leaves=3)

        painter.end()

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


class Bubble(QFrame):
    """一条消息。透明容器；正文按段落拆成白底子容器。"""

    def __init__(self, role, text, media=None, ts=""):
        super().__init__()
        self.role = role
        self.bodies = []
        self.setStyleSheet("QFrame#msgCard { background: transparent; border: none; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
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
        name = QLabel({"user": "你", "assistant": "薇薇安娜"}.get(role, role))
        name.setObjectName("muted")
        top.addWidget(name)
        stamp = QLabel(str(ts)[-8:-3] if ts else "")
        stamp.setObjectName("stamp")
        top.addWidget(stamp)
        top.addStretch(1)
        layout.addLayout(top)

        paragraphs = [block.strip() for block in str(text).split("\n") if block.strip()]
        if not paragraphs:
            paragraphs = [""]
        for paragraph in paragraphs:
            body = QLabel(paragraph)
            body.setWordWrap(False)
            body.setProperty("raw", paragraph)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.setObjectName("bubbleBody")
            body.setStyleSheet(
                "QLabel#bubbleBody { background: rgba(255,255,255,0.64); padding: 10px 12px;"
                " border-radius: 6px; }")
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
        self.expanded = True
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
    """顶栏。时钟不参与布局，按整个宽度算中心，保证正中对齐对话区。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clock = QLabel("◷", self)
        self.clock.setObjectName("clock")
        self.clock.setAlignment(Qt.AlignCenter)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.clock.adjustSize()
        x = (self.width() - self.clock.width()) // 2
        y = (self.height() - self.clock.height()) // 2
        self.clock.move(max(0, x), max(0, y))
        self.clock.raise_()


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

        self._poll_timer = _QT(self)
        self._poll_timer.setInterval(6000)
        self._poll_timer.timeout.connect(self._poll_history)
        self._poll_timer.start()
        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence("F5"), self, activated=self.reload_all)
        self._seen = 0

        row_outer = QHBoxLayout(self)
        row_outer.setContentsMargins(0, 0, 0, 0)
        row_outer.setSpacing(0)

        outer = QVBoxLayout()
        outer.setContentsMargins(32, 18, 32, 14)
        outer.setSpacing(8)

        self.header = HeaderBar()
        head = QHBoxLayout(self.header)
        head.setSpacing(18)

        left = QWidget()
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(18)
        title = QLabel("对话")
        title.setObjectName("h1")
        left_layout.addWidget(title)
        self.info = QLabel("")
        self.info.setObjectName("info")
        left_layout.addWidget(self.info)
        left_layout.addStretch(1)
        head.addWidget(left, 1)

        head.addStretch(1)
        self.model_btn = QPushButton()
        self.model_btn.setObjectName("picker")
        self.model_btn.setCursor(Qt.PointingHandCursor)
        self.model_btn.clicked.connect(self.open_picker)
        head.addWidget(self.model_btn)
        outer.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.stack = QWidget()
        self.messages = QVBoxLayout(self.stack)
        self.messages.setSpacing(14)
        self.messages.addStretch(1)
        self.scroll.setWidget(self.stack)
        self.scroll.viewport().setAutoFillBackground(False)
        self.stack.setAutoFillBackground(False)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._follow = True
        self._vbar = self.scroll.verticalScrollBar()
        self._vbar.rangeChanged.connect(self._on_range)
        self._vbar.valueChanged.connect(self._on_scroll)
        self.scroll.setContextMenuPolicy(Qt.CustomContextMenu)
        self.scroll.customContextMenuRequested.connect(self._chat_menu)
        outer.addWidget(self.scroll, 1)

        bar = QFrame()
        bar.setObjectName("usageBar")
        bar.setStyleSheet("QFrame#usageBar { border: none; background: transparent; }")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(4, 2, 4, 0)
        self.usage = QLabel("usage —")
        self.usage.setObjectName("usage")
        bar_layout.addWidget(self.usage)
        bar_layout.addStretch(1)
        self.pending_label = QLabel("")
        self.pending_label.setObjectName("muted")
        bar_layout.addWidget(self.pending_label)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("说点什么…")
        self.input.returnPressed.connect(self.send)
        send_btn = QPushButton("发送")
        send_btn.clicked.connect(self.send)
        row.addWidget(self.input, 1)
        row.addWidget(send_btn)
        outer.addLayout(row)
        outer.addWidget(bar)

        self.load_history()
        try:
            self._on_usage(api_get("/usage") or {})
        except Exception:                    # noqa: BLE001
            pass
        self.refresh_state()
        holder = QWidget()
        holder.setLayout(outer)
        row_outer.addWidget(holder, 1)

        self.panel = StatePanel(self)
        self.panel.hide()
        self._last_message_at = 0
        self.refresh_state()
        self._tick_clock()

        self.refresh_model_text()

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
        import time as _t
        from datetime import datetime
        now = datetime.now()
        stamp = now.strftime("%H:%M")
        idle = ""
        last = getattr(self, "_last_message_at", 0) or 0
        if last:
            secs = max(0, int(_t.time() - float(last)))
            if secs >= 3600:
                idle = f"已静默 {secs // 3600} 小时 {secs % 3600 // 60} 分"
            elif secs >= 60:
                idle = f"已静默 {secs // 60} 分"
            else:
                idle = f"已静默 {secs} 秒"
        self.header.clock.setText(f"◷　{idle}" if idle else "◷")
        self.header.clock.adjustSize()
        x = (self.header.width() - self.header.clock.width()) // 2
        self.header.clock.move(max(0, x), self.header.clock.y())

    def toggle_panel(self):
        show = not self.panel.isVisible()
        if show:
            self.panel.setGeometry(0, 0, 230, self.height())
            self.panel.show()
            self.panel.raise_()
            self.refresh_state()
        else:
            self.panel.hide()
        self.state_btn.setChecked(show)

    # ---------- 模型 / 思考强度 ----------

    def refresh_model_text(self):
        self.model_btn.setText(f"{self.model_name}　·　{self.think_level}　▾")

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
        menu.addSeparator()
        head2 = menu.addAction("推理等级")
        head2.setEnabled(False)
        for level in ("Off", "Low", "High", "Max"):
            act = menu.addAction(level)
            act.setCheckable(True)
            act.setChecked(level == self.think_level)
            act.triggered.connect(lambda _=False, lv=level: self.pick_think(lv))
        menu.exec(self.model_btn.mapToGlobal(self.model_btn.rect().bottomLeft()))

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
        try:
            s = api_get("/state")
        except Exception as e:               # noqa: BLE001
            self.panel.update_text(f"读不到状态：{e}")
            return
        self._last_message_at = s.get("lastMessageAt") or self._last_message_at
        pos = (s.get("local") or "未知").split("/")[-1]
        state_txt = "睡着" if s.get("active") == "sleep" else "醒着"
        self.info.setText(f"角色位置：{pos}　　状态：{state_txt}")
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
        """清掉界面上的消息，从头拉一遍会话和状态。"""
        self._clear_bubbles()
        self.refresh_state()
        self.load_history()
        self.scroll_to_bottom()

    def add_bubble(self, role, text, media=None, ts="", split=True):
        if not ts:
            import time as _t
            ts = _t.strftime("%Y-%m-%d %H:%M:%S")
        parts = [text]
        if not parts:
            parts = [""]
        for index, part in enumerate(parts):
            bubble = Bubble(role, part, media if index == 0 else None,
                            ts if index == 0 else "")
            self.messages.insertWidget(self.messages.count() - 1, bubble)
            self._fit_width(bubble)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def send(self):
        text = self.input.text().strip()
        if not text or (self.worker and self.worker.isRunning()):
            return
        self.input.clear()
        self._follow = True
        self.add_bubble("user", text)
        self.scroll_to_bottom()
        self.add_bubble("assistant", "", split=False)
        self._stream_bubble = self.messages.itemAt(self.messages.count() - 2).widget()
        self._stream_label = self._stream_bubble.findChildren(QLabel)[-1]
        self._buffer = ""
        self._tip_group = None


        self.worker = TurnWorker(text)
        self.worker.tool.connect(self._on_tool)
        self.worker.usage.connect(self._on_usage)
        self.worker.delta.connect(self._on_delta)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_error)
        self.worker.start()

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
        self._tip_group.body.show()

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
        # 这一轮已经流式画过了，把轮询游标推到服务端末尾，避免重复渲染
        try:
            data = api_get("/history?limit=20")
            self._seen = int(data.get("total") or 0)
        except Exception:                    # noqa: BLE001
            pass
        self.refresh_state()

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
        title = QLabel("配置")
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

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"PhilosophicalZombie · {BUILD}")
        self.resize(1180, 760)

        self.backdrop = Backdrop(self)
        self.backdrop.lower()

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        nav = QWidget()
        nav.setFixedWidth(96)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 16, 0, 16)
        nav_layout.setSpacing(2)

        self.pages = QStackedWidget()
        self.buttons = []
        for index, (label, page) in enumerate([
                ("对话", ChatPage()), ("配置", ConfigPage()),
                ("识域", RealmPage()), ("技能", SkillPage())]):
            btn = QPushButton(label)
            btn.setObjectName("nav")
            btn.setCheckable(True)
            btn.setChecked(index == 0)
            btn.clicked.connect(lambda _=False, i=index: self.switch(i))
            nav_layout.addWidget(btn)
            self.buttons.append(btn)
            self.pages.addWidget(page)
        nav_layout.addStretch(1)

        layout.addWidget(nav)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.setStyleSheet(QSS)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.backdrop.setGeometry(0, 0, self.width(), self.height())
        self.backdrop.lower()

    def switch(self, index):
        for i, btn in enumerate(self.buttons):
            btn.setChecked(i == index)
        self.pages.setCurrentIndex(index)


def main():
    app = QApplication(sys.argv)
    print(f"[UI] build={BUILD}  server={BASE}", flush=True)
    font = QFont("Microsoft YaHei UI", 10)
    app.setFont(font)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
