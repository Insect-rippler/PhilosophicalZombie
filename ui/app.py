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
from PySide6.QtGui import QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSplitter,
                               QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

BASE = os.environ.get("PZ_SERVER", "http://127.0.0.1:8765")
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
QWidget {{ background: {PAPER}; color: #23211e; font-size: 13.5px; }}
QLabel#h1 {{ font-size: 19px; font-weight: 600; letter-spacing: 2px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#info {{ color: #a8a49c; font-size: 12px; }}
QLabel#stamp {{ color: #b3aea6; font-size: 10px; }}
QLabel#clock {{ color: #9d988f; font-size: 12px; padding: 0 14px; }}
QFrame#card {{ background: #ffffff; border: 1px solid {LINE}; border-radius: 6px; }}
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
QPushButton#nav:checked {{ background: #ffffff; border-left: 2px solid {ACCENT};
                           color: #23211e; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox {{
    border: 1px solid {LINE}; background: #ffffff; padding: 7px 9px; border-radius: 5px; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #d8d5cf; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QListWidget {{ border: 1px solid {LINE}; background: #ffffff; border-radius: 6px; }}
QListWidget::item {{ padding: 8px 10px; }}
QListWidget::item:selected {{ background: #efece6; color: #23211e; }}
QPushButton#tipHeader {{ border: none; background: transparent; color: {MUTED};
                         padding: 0; text-align: left; font-size: 11px; }}
QPushButton#tipHeader:hover {{ color: {ACCENT}; }}
QPushButton#picker {{ border: 1px solid {LINE}; padding: 5px 14px; background: #ffffff;
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
                            self.done.emit(value)
                        elif kind == "error":
                            self.failed.emit(str(value))
        except Exception as e:               # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            if parts:
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


class Bubble(QFrame):
    """一条消息。透明容器；正文按段落拆成白底子容器。"""

    def __init__(self, role, text, media=None, ts=""):
        super().__init__()
        self.role = role
        self.bodies = []
        self.setStyleSheet("QFrame { background: transparent; border: none; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        if role == "narration":
            label = QLabel(text)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignCenter)
            label.setObjectName("muted")
            font = label.font()
            font.setItalic(True)
            label.setFont(font)
            layout.addWidget(label)
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
            body = QLabel(wrap_chars(paragraph))
            body.setWordWrap(False)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.setObjectName("bubbleBody")
            body.setStyleSheet(
                "QLabel#bubbleBody { background: #ffffff; padding: 10px 12px;"
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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.header = QPushButton()
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
        label = QLabel(f"  {text}")
        label.setObjectName("muted")
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
        outer.addWidget(self.scroll, 1)

        bar = QFrame()
        bar.setObjectName("usageBar")
        bar.setStyleSheet("QFrame#usageBar { border: none; background: transparent; }")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(4, 2, 4, 0)
        self.usage = QLabel("usage —")
        self.usage.setObjectName("muted")
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
            rows = api_get("/history?limit=40").get("messages") or []
        except Exception:                    # noqa: BLE001
            return
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
            if role == "narration":
                self.add_bubble("narration", text, ts=ts)
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
        self.add_bubble("user", text)
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
        bodies = getattr(bubble, "bodies", None)
        if not bodies:
            return
        limit = int(self.scroll.viewport().width() * 0.9)
        widest = 160
        for body in bodies:
            body.ensurePolished()
            body.setMaximumWidth(16777215)
            fm = QFontMetrics(body.font())
            longest = max(fm.horizontalAdvance(line)
                          for line in body.text().splitlines()) if body.text() else 0
            width = min(limit, max(160, longest + 40))
            body.setFixedWidth(width)
            body.setMinimumHeight(fm.height() * max(1, len(body.text().splitlines())) + 24)
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

    def _on_tool(self, payload):
        kind = str(payload.get("kind") or "tip")
        label = str(payload.get("text") or payload.get("name") or "")
        if not label:
            return
        if kind == "narration":
            self.add_bubble("narration", label)
            return
        if self._tip_group is None:
            self._tip_group = TipGroup()
            self.messages.insertWidget(self.messages.count() - 1, self._tip_group)
        self._tip_group.add(label)
        self._tip_group.body.show()

    def _on_delta(self, text):
        self._buffer = getattr(self, "_buffer", "") + text
        label = getattr(self, "_stream_label", None)
        if label is not None:
            label.setText(self._buffer)
            bubble = getattr(self, "_stream_bubble", None)
            if bubble is not None:
                self._fit_width(bubble)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_done(self, text):
        label = getattr(self, "_stream_label", None)
        if label is not None and text and text != getattr(self, "_buffer", ""):
            label.setText(text)
        if self._tip_group is not None:
            self._tip_group.collapse()
            self._tip_group = None
        self.refresh_state()

    def _on_error(self, message):
        label = getattr(self, "_stream_label", None)
        if label is not None:
            label.setText(f"[连接出错] {message}")


# ---------------- 配置页 ----------------

class ConfigPage(QWidget):
    FIELDS = [("model", "模型"), ("temperature", "温度"),
              ("max_tokens", "最大输出"), ("reasoning_effort", "思考强度")]

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel("配置")
        title.setObjectName("h1")
        layout.addWidget(title)

        self.edits = {}
        for key, label in self.FIELDS:
            row = QHBoxLayout()
            name = QLabel(label)
            name.setFixedWidth(90)
            edit = QLineEdit()
            row.addWidget(name)
            row.addWidget(edit, 1)
            layout.addLayout(row)
            self.edits[key] = edit

        save = QPushButton("保存")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        layout.addStretch(1)
        self.load()

    def load(self):
        try:
            cfg = api_get("/config").get("payload") or {}
        except Exception:                    # noqa: BLE001
            return
        for key, _ in self.FIELDS:
            value = cfg.get(key)
            self.edits[key].setText("" if value is None else str(value))

    def save(self):
        payload = {}
        for key, _ in self.FIELDS:
            text = self.edits[key].text().strip()
            if not text:
                continue
            payload[key] = int(text) if text.isdigit() else text
        api_send("POST", "/config", {"payload": payload})


# ---------------- 识域页 ----------------

class RealmPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)

        left = QWidget()
        left.setFixedWidth(200)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("识域")
        title.setObjectName("h1")
        left_layout.addWidget(title)
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self.load_file)
        left_layout.addWidget(self.list, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        right_layout.addWidget(self.editor, 1)
        row = QHBoxLayout()
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        save = QPushButton("保存")
        save.clicked.connect(self.save_file)
        row.addWidget(self.hint, 1)
        row.addWidget(save)
        right_layout.addLayout(row)

        layout.addWidget(left)
        layout.addWidget(right, 1)
        self.load_list()

    def load_list(self):
        try:
            files = api_get("/realm").get("files") or []
        except Exception:                    # noqa: BLE001
            files = []
        self.list.addItems(files)

    def load_file(self, name):
        if not name:
            return
        try:
            data = api_get(f"/realm/{name}")
        except Exception as e:               # noqa: BLE001
            self.hint.setText(f"读取失败：{e}")
            return
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
                ("对话", ChatPage()), ("配置", ConfigPage()), ("识域", RealmPage())]):
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
