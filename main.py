"""DeepSeek 娘化桌宠：学习计划监督版。

依赖：Python 3.10+、PySide6。API 是可选项，没有 key 也能完整运行。
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, QTimer, Signal, QLockFile, QUrl
from PySide6.QtGui import QAction, QFont, QIcon, QPainter, QPixmap, QDesktopServices
from companion import (
    CHAT_INTERVAL,
    KIND_LABELS,
    ChatScheduler,
    api_chat_message,
    build_context,
    local_chat_message,
)
from supervision import Supervisor, INTERVAL
from windows_session import WindowsSession
from markdown_it import MarkdownIt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


APP_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = APP_DIR.parents[1]
PLAN_PATH = WORKSPACE_DIR / "学习计划_当前.md"
CONFIG_PATH = APP_DIR / "config.json"
ASSET_PATH = APP_DIR / "assets" / "deepseek_chibi.png"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# 生气气泡显示 5 分钟后自动收起，生气动画继续循环。
# 不能改成 0：duration=0 的气泡永不消失，会永久占住气泡位，陪聊再也没有机会开口。
ANGRY_BUBBLE_MS = 5 * 60 * 1000


def startup_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, "DeepseekStudyPet")
        return True
    except OSError:
        return False


def set_startup(enabled: bool) -> None:
    import winreg
    runner = Path(sys.executable).with_name("pythonw.exe")
    if not runner.exists():
        runner = Path(sys.executable)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, "DeepseekStudyPet", 0, winreg.REG_SZ, f'"{runner}" "{APP_DIR / "main.py"}"')
        else:
            try:
                winreg.DeleteValue(key, "DeepseekStudyPet")
            except FileNotFoundError:
                pass


DEFAULT_CONFIG = {
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_enabled": False,
    "reminder_enabled": True,
    "chat_enabled": False,
    "startup_enabled": True,
    "reminder_interval_minutes": 30,
    "chat_interval_minutes": 15,
    "startup_reminder_seconds": 3,
    "pet_name": "Deepseek",
    "personality": "顽劣和温柔并存，带一点傲娇",
}


def load_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config.update(data)
        except (OSError, json.JSONDecodeError):
            pass
    if not config.get("api_key"):
        config["api_enabled"] = False
    # V2 uses fixed reminder/response timing. Old wall-clock timestamps are not progress.
    config["reminder_interval_minutes"] = 30
    # 陪聊间隔同样固定；开关由右键菜单或任务面板控制，默认关闭。
    config["chat_interval_minutes"] = CHAT_INTERVAL // 60
    config["chat_enabled"] = bool(config.get("chat_enabled", False))
    config.pop("last_progress_at", None)
    config.pop("pending_tasks", None)
    try:
        seconds = float(config.get("supervision_seconds", 0))
        config["supervision_seconds"] = seconds if math.isfinite(seconds) and seconds >= 0 else 0
    except (ValueError, TypeError):
        config["supervision_seconds"] = 0
    for name, low, high in (("startup_reminder_seconds",1,60),):
        try:
            config[name] = max(low,min(high,int(config[name])))
        except (TypeError,ValueError):
            config[name] = DEFAULT_CONFIG[name]
    return config


def save_config(config: dict) -> None:
    pending = CONFIG_PATH.with_suffix(".tmp")
    pending.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(CONFIG_PATH)


@dataclass
class Task:
    number: str
    title: str
    status: str
    line_index: int


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def plain_title(text: str) -> str:
    children = MarkdownIt().parseInline(text)[0].children or []
    return "".join(t.content for t in children if t.type in ("text", "code_inline"))


def read_tasks(path: Path = PLAN_PATH) -> list[Task]:
    """读取学习计划里的 P0 表格，保留原行号供回写使用。"""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    in_p0 = False
    tasks: list[Task] = []
    for index, line in enumerate(lines):
        if line.startswith("## P0"):
            in_p0 = True
            continue
        if in_p0 and line.startswith("## "):
            break
        if not in_p0 or not line.lstrip().startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) < 5 or not cells[0].isdigit() or set(cells[0]) == {"-"}:
            continue
        tasks.append(Task(cells[0], plain_title(cells[1]), cells[-1], index))
    return tasks


def complete_tasks(task_numbers: set[str], path: Path = PLAN_PATH) -> None:
    if not task_numbers or not path.exists():
        return
    original = path.read_bytes()
    lines = original.decode("utf-8-sig").splitlines(keepends=True)
    today = datetime.now().strftime("%Y-%m-%d")
    in_p0 = False
    for index, line in enumerate(lines):
        if line.startswith("## P0"):
            in_p0 = True
            continue
        if in_p0 and line.startswith("## "):
            break
        if not in_p0 or not line.lstrip().startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) < 5 or cells[0] not in task_numbers:
            continue
        if cells[-1].startswith("已完成"):
            continue
        end = line.rfind("|")
        start = line.rfind("|",0,end)
        lines[index] = line[:start+1] + f" 已完成（{today}） " + line[end:]
    if path.read_bytes() != original:
        raise OSError("学习计划刚被修改，请重新打开任务列表。")
    updated = "".join(lines).encode("utf-8")
    if original.startswith(b"\xef\xbb\xbf"):
        updated = b"\xef\xbb\xbf" + updated
    if updated != original:
        path.with_suffix(".md.bak").write_bytes(original)
        pending = path.with_suffix(".md.tmp")
        pending.write_bytes(updated)
        pending.replace(path)


def local_message(tasks: list[Task], config: dict, urgent: bool = False) -> str:
    name = config.get("pet_name", "Deepseek")
    if not tasks:
        return f"{name}：今天的清单已经清空啦。哼，我才不是在夸你。"
    first = "\n".join(f"{i+1}. {task.title}" for i, task in enumerate(tasks[:3]))
    if urgent:
        return f"{name}：哼，提醒过你了还不理我。快从这项开始：{tasks[0].title}。"
    return f"{name}：今天先做这几件事：\n{first}\n我会陪着你，别想假装没看见。"


def api_message(tasks: list[Task], config: dict, urgent: bool = False) -> str | None:
    key = str(config.get("api_key", "")).strip()
    if not key or not config.get("api_enabled", False):
        return None
    task_text = "\n".join(f"- {task.title}" for task in tasks[:5]) or "暂无未完成任务"
    prompt = (
        "你是桌面宠物 Deepseek。性格顽劣和温柔并存，带一点傲娇。"
        "用简短中文提醒用户学习，最多两句话，不要解释规则。"
        f"当前任务：\n{task_text}\n"
        + ("有效在线一小时没有完成记录，且提醒后仍未回应。语气傲娇急切，不要声称用户没有学习或辱骂。" if urgent else "语气轻快，给出下一步行动。")
    )
    url = str(config.get("base_url", "https://api.deepseek.com")).rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": config.get("model", "deepseek-chat"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 80,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except (OSError, KeyError, IndexError, AttributeError, TypeError, ValueError):
        return None


class ApiWorker(QThread):
    result = Signal(object)

    def __init__(self, tasks: list[Task], config: dict, urgent: bool) -> None:
        super().__init__()
        self.tasks = tasks
        self.config = config.copy()
        self.urgent = urgent

    def run(self) -> None:
        self.result.emit(api_message(self.tasks, self.config, self.urgent))


class ChatWorker(QThread):
    """陪聊文案的请求线程。与提醒线程分开，避免互相排队或互相取消。"""

    result = Signal(object)

    def __init__(self, kind: str, context: dict, config: dict) -> None:
        super().__init__()
        self.kind = kind
        self.context = dict(context)
        self.config = config.copy()

    def run(self) -> None:
        self.result.emit(api_chat_message(self.kind, self.context, self.config))


class SpeechBubble(QWidget):
    responded = Signal(int)

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Deepseek 提醒")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("bubblePanel")
        panel.setStyleSheet("QFrame#bubblePanel {background:#ffffff;border:1px solid #8a98bf;border-radius:8px;} QLabel {color:#28334e;font-size:13px;} QPushButton {padding:7px;background:#edf1fa;border:1px solid #c7d1e8;border-radius:5px;color:#28334e;}")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        top.addWidget(QLabel("Deepseek"))
        top.addStretch()
        self.dismiss = QToolButton()
        self.dismiss.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
        self.dismiss.setToolTip("关闭提示")
        self.dismiss.clicked.connect(self.hide)
        top.addWidget(self.dismiss)
        layout.addLayout(top)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label)
        self.controls = QWidget()
        buttons = QHBoxLayout(self.controls)
        buttons.setContentsMargins(0, 5, 0, 0)
        self.working = QPushButton("我在做")
        self.working.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.later = QPushButton("稍后提醒")
        self.later.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.working.clicked.connect(lambda: self.responded.emit(60))
        self.later.clicked.connect(lambda: self.responded.emit(30))
        buttons.addWidget(self.working)
        buttons.addWidget(self.later)
        layout.addWidget(self.controls)
        root.addWidget(panel)
        self.setFixedWidth(350)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def show_message(self, text: str, anchor: QPoint, duration=12000, controls=False) -> None:
        self.controls.setVisible(controls)
        self.update_text(text)
        self.follow(anchor)
        self.show()
        self.raise_()
        self.hide_timer.stop()
        if duration:
            self.hide_timer.start(duration)

    def update_text(self, text):
        self.label.setText(text)
        # A wrapped QLabel's default size hint can clip multi-line reminders on Windows.
        panel = self.label.parentWidget()
        margins = panel.layout().contentsMargins()
        width = self.width() - margins.left() - margins.right() - 2 * panel.frameWidth()
        self.label.setMinimumHeight(0)
        self.label.setMaximumHeight(16777215)
        self.label.setFixedHeight(self.label.heightForWidth(width))
        panel.layout().activate()
        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()

    def follow(self, anchor: QPoint) -> None:
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        x = max(bounds.left(),min(anchor.x()-self.width(),bounds.right()-self.width()+1))
        y = max(bounds.top(),min(anchor.y()-self.height()-8,bounds.bottom()-self.height()+1))
        self.move(x,y)


class PetWidget(QWidget):
    clicked = Signal()
    right_clicked = Signal(QPoint)
    moved = Signal()
    dropped = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Deepseek 桌宠")
        self.setFixedSize(176, 190)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.frame = 0
        self.state = "normal"
        self.drag_offset: QPoint | None = None
        self.dragged = False
        self.press_point = QPoint()
        self.pixmap = QPixmap(str(ASSET_PATH)) if ASSET_PATH.exists() else QPixmap()
        self.frames = {}
        for state in ("normal", "angry"):
            frames = [QPixmap(str(APP_DIR / "assets" / "frames" / f"{state}_{i:02}.png")) for i in range(6)]
            if all(not frame.isNull() for frame in frames):
                self.frames[state] = frames
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)
        self.timer.start(180)

    def set_state(self, state: str) -> None:
        if state not in ("normal", "angry"):
            raise ValueError(f"Unknown animation: {state}")
        if self.state == state:
            return
        self.state = state
        self.frame = 0
        self.timer.start(180)
        self.update()

    def advance(self) -> None:
        self.frame = (self.frame + 1) % 6
        self.timer.setInterval(180 + ((3000 if self.state == "normal" else 600) if self.frame == 5 else 0))
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit(event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.pos()
            self.press_point = event.globalPosition().toPoint()
            self.dragged = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self.drag_offset
            if (event.globalPosition().toPoint() - self.press_point).manhattanLength() > QApplication.startDragDistance():
                self.dragged = True
            if self.dragged:
                self.move(new_pos)
                self.moved.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.drag_offset = None
            if self.dragged:
                self.clamp_position()
                self.dropped.emit()
            else:
                self.clicked.emit()

    def clamp_position(self) -> None:
        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        self.move(max(bounds.left(),min(self.x(),bounds.right()-self.width()+1)),
                  max(bounds.top(),min(self.y(),bounds.bottom()-self.height()+1)))
        self.moved.emit()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        frames = self.frames.get(self.state)
        pixmap = frames[self.frame] if frames else self.pixmap
        painter.drawPixmap(QRect(8, 10, 160, 160), pixmap)


class TaskDialog(QDialog):
    remind_now = Signal()
    responded = Signal(int)

    def __init__(self, tasks: list[Task], config: dict, on_save: Callable[[set[str]], None], parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.on_save = on_save
        self.snapshot = tasks
        self.setWindowTitle("Deepseek · 今日任务")
        self.setMinimumWidth(470)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(
            "QDialog { background: #f7f8fa; } QLabel#title { color:#28335f; font-size:21px; font-weight:700; } "
            "QLabel#sub { color:#737b99; font-size:12px; } QListWidget { border:1px solid #dfe3f2; "
            "border-radius:6px; background:white; padding:6px; } QListWidget::item { padding:10px; } "
            "QPushButton { background:#526bc5; color:white; border:0; border-radius:8px; padding:9px 14px; } "
            "QPushButton:hover { background:#4059b2; } QCheckBox { color:#303753; } QSpinBox { padding:6px; }"
        )
        root = QVBoxLayout(self)
        title = QLabel("Deepseek · 今日任务")
        title.setObjectName("title")
        root.addWidget(title)
        subtitle = QLabel(f"P0 · 本周    {sum(not t.status.startswith('已完成') for t in tasks)} 项待完成")
        subtitle.setObjectName("sub")
        root.addWidget(subtitle)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        for task in tasks:
            item = QListWidgetItem(task.title)
            item.setData(Qt.ItemDataRole.UserRole, task.number)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if task.status.startswith("已完成") else Qt.CheckState.Unchecked)
            if task.status.startswith("已完成"):
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setToolTip(task.status)
            height = self.list.fontMetrics().boundingRect(0, 0, 410, 200, Qt.TextFlag.TextWordWrap, task.title).height() + 24
            item.setSizeHint(QSize(0, max(42, height)))
            self.list.addItem(item)
        if not tasks:
            empty = QListWidgetItem("今天没有待完成的 P0 任务，去喝口水吧。")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(empty)
        root.addWidget(self.list)

        response_row = QHBoxLayout()
        self.working = QPushButton("我在做")
        self.working.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.later = QPushButton("稍后提醒")
        self.later.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.working.clicked.connect(lambda: self.responded.emit(60))
        self.later.clicked.connect(lambda: self.responded.emit(30))
        response_row.addWidget(self.working)
        response_row.addWidget(self.later)
        response_row.addStretch()
        root.addLayout(response_row)
        self.response_status = QLabel("")
        root.addWidget(self.response_status)

        settings = QFrame()
        settings.setStyleSheet("QFrame { background:transparent; }")
        form = QFormLayout(settings)
        self.enabled = QCheckBox("开启提醒")
        self.enabled.setChecked(bool(config.get("reminder_enabled", True)))
        self.api_enabled = QCheckBox("使用 API 生成文案")
        self.api_enabled.setChecked(bool(config.get("api_enabled", False) and config.get("api_key")))
        self.api_enabled.setEnabled(bool(config.get("api_key")))
        self.api_enabled.setToolTip("仅发送本次待办标题给 DeepSeek；无 key 时使用本地文案")
        self.autostart = QCheckBox("登录 Windows 后启动")
        self.autostart.setChecked(bool(config.get("startup_enabled",True)))
        self.chat_enabled = QCheckBox("每 15 分钟主动陪聊")
        self.chat_enabled.setChecked(bool(config.get("chat_enabled", False)))
        self.chat_enabled.setToolTip("与学习提醒无关的日常消息：" + "、".join(KIND_LABELS.values()))
        form.addRow(self.enabled)
        form.addRow(self.chat_enabled)
        form.addRow("提醒间隔", QLabel("30 分钟"))
        form.addRow(self.autostart)
        form.addRow(self.api_enabled)
        root.addWidget(settings)

        buttons = QDialogButtonBox()
        save = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        remind = buttons.addButton("现在提醒我", QDialogButtonBox.ButtonRole.ActionRole)
        remind.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        close = buttons.addButton("关闭", QDialogButtonBox.ButtonRole.RejectRole)
        close.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton))
        save.clicked.connect(self.save)
        remind.clicked.connect(self.remind_now.emit)
        close.clicked.connect(self.reject)
        root.addWidget(buttons)

    def save(self) -> None:
        selected = {
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).data(Qt.ItemDataRole.UserRole)
            and self.list.item(i).checkState() == Qt.CheckState.Checked
        }
        self.config["reminder_enabled"] = self.enabled.isChecked()
        self.config["chat_enabled"] = self.chat_enabled.isChecked()
        self.config["api_enabled"] = self.api_enabled.isChecked() and bool(self.config.get("api_key"))
        self.config["startup_enabled"] = self.autostart.isChecked()
        try:
            latest = {t.number:t.title for t in read_tasks()}
            if any(latest.get(t.number) != t.title for t in self.snapshot if t.number in selected):
                raise OSError("学习计划任务已变，请关闭并重新打开清单。")
            self.on_save(selected)
        except OSError as error:
            QMessageBox.warning(self,"保存失败",str(error))
            return
        self.accept()


class DesktopPet:
    def __init__(self) -> None:
        self.app = QApplication.instance()
        self.config = load_config()
        self.supervisor = Supervisor(no_progress=self.config["supervision_seconds"])
        self.tasks: list[Task] = []
        snapshot = self.config.get("task_status_snapshot", {})
        self.task_status = snapshot if isinstance(snapshot, dict) else {}
        self.plan_valid = False
        self.pet = PetWidget()
        self.bubble = SpeechBubble()
        self.dialog: TaskDialog | None = None
        self.api_threads: set[ApiWorker] = set()
        self.chat_threads: set[ChatWorker] = set()
        self.chat = ChatScheduler()
        self.request_id = 0
        self.closing = False
        self.last_api_at = 0.0
        self.last_chat_api_at = 0.0
        self.last_tick = time.monotonic()
        self.last_plan_check = 0.0
        self.last_save = time.monotonic()
        self.session = WindowsSession(self.app, self.pet, self.pet)
        self.session.changed.connect(self.on_session_changed)
        self.tray = QSystemTrayIcon(self.pet)
        self.tray.setIcon(self._tray_icon())
        self._build_tray_menu()
        self.pet.clicked.connect(self.open_tasks)
        self.pet.right_clicked.connect(self.show_context_menu)
        self.pet.moved.connect(self.follow_bubble)
        self.pet.dropped.connect(self.save_position)
        self.bubble.responded.connect(self.respond)
        self.tick_timer = QTimer(self.pet)
        self.tick_timer.timeout.connect(self.tick)
        self.startup_timer = QTimer(self.pet)
        self.startup_timer.setSingleShot(True)
        self.startup_timer.timeout.connect(self.welcome)
        self.app.aboutToQuit.connect(self.stop_workers)

    def _tray_icon(self) -> QIcon:
        if not ASSET_PATH.exists():
            return self.app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        return QIcon(str(ASSET_PATH))

    def _build_tray_menu(self) -> None:
        menu = QMenu()
        menu.setStyleSheet("QMenu { background:#ffffff; color:#28335f; padding:6px; } QMenu::item { padding:7px 24px; }")
        open_action = QAction("打开今日任务", menu)
        open_action.triggered.connect(self.open_tasks)
        remind_action = QAction("现在提醒", menu)
        remind_action.triggered.connect(lambda: self.remind())
        self.pause_action = QAction("暂停提醒", menu)
        self.pause_action.setCheckable(True)
        self.pause_action.setChecked(not self.config.get("reminder_enabled",True))
        self.pause_action.triggered.connect(self.toggle_pause)
        settings_action = QAction("打开配置文件夹", menu)
        settings_action.triggered.connect(lambda: self.show_config_location())
        self.startup_action = QAction("开机启动", menu)
        self.startup_action.setCheckable(True)
        self.startup_action.setChecked(startup_enabled())
        self.startup_action.triggered.connect(self.toggle_startup)
        exit_action = QAction("退出桌宠", menu)
        exit_action.triggered.connect(self.app.quit)
        self.chat_action = QAction("每 15 分钟陪聊", menu)
        self.chat_action.setCheckable(True)
        self.chat_action.setChecked(bool(self.config.get("chat_enabled", False)))
        self.chat_action.setToolTip("与学习提醒无关的日常消息：" + "、".join(KIND_LABELS.values()))
        self.chat_action.triggered.connect(self.toggle_chat)
        chat_now_action = QAction("现在说一句", menu)
        chat_now_action.triggered.connect(self.chat_now)
        menu.addAction(open_action)
        menu.addAction(remind_action)
        menu.addSeparator()
        menu.addAction(self.pause_action)
        menu.addAction(chat_now_action)
        menu.addAction(self.chat_action)
        menu.addAction(self.startup_action)
        self.visible_action = QAction("显示桌宠", menu)
        self.visible_action.setCheckable(True)
        self.visible_action.setChecked(True)
        self.visible_action.triggered.connect(self.toggle_visible)
        menu.addAction(self.visible_action)
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)

    def start(self) -> None:
        screen = self.app.primaryScreen().availableGeometry()
        self.pet.move(screen.right() - self.pet.width() - 36, screen.bottom() - self.pet.height() - 28)
        position = self.config.get("position")
        if isinstance(position,list) and len(position)==2 and all(isinstance(v,int) for v in position):
            self.pet.move(*position)
        self.pet.clamp_position()
        self.pet.show()
        self.tray.show()
        if self.config.get("startup_enabled",True) and not startup_enabled():
            self.toggle_startup(True)
        self.sync_progress()
        self.refresh_availability()
        if self.supervisor.active:
            self.startup_timer.start(int(self.config["startup_reminder_seconds"]) * 1000)
        self.last_tick = time.monotonic()
        self.tick_timer.start(1000)

    def persist_progress(self) -> None:
        self.config["supervision_seconds"] = self.supervisor.no_progress
        self.config["task_status_snapshot"] = self.task_status
        save_config(self.config)
        self.last_save = time.monotonic()

    def invalidate_message(self) -> None:
        self.request_id += 1
        self.bubble.hide_timer.stop()
        self.bubble.hide()

    def refresh_availability(self) -> None:
        available = (not self.closing and self.plan_valid and bool(self.tasks)
                     and self.config.get("reminder_enabled", True)
                     and self.pet.isVisible() and self.session.available)
        if not available:
            if self.supervisor.active or self.supervisor.angry or self.supervisor.response_left is not None:
                self.supervisor.suspend()
                self.invalidate_message()
                self.persist_progress()
            self.pet.set_state("normal")
            self.startup_timer.stop()
        elif not self.supervisor.active:
            self.supervisor.active = True
            self.last_tick = time.monotonic()

    def on_session_changed(self, available: bool) -> None:
        # Event boundaries discard fractional elapsed time rather than charging offline time.
        self.last_tick = time.monotonic()
        self.refresh_availability()

    def tick(self) -> None:
        if self.closing:
            return
        now = time.monotonic()
        seconds = now - self.last_tick
        self.last_tick = now
        chat_seconds = seconds
        was_active = self.supervisor.active
        self.session.refresh()
        if seconds > 5:
            # A missing power event, suspended process, or event-loop stall must not count as neglect.
            self.supervisor.suspend()
            self.invalidate_message()
            self.pet.set_state("normal")
            self.persist_progress()
            seconds = 0
            chat_seconds = 0
        if now - self.last_plan_check >= 5:
            self.last_plan_check = now
            if self.sync_progress():
                seconds = 0
        self.refresh_availability()
        if not was_active:
            seconds = 0
        self.advance_supervision(seconds)
        self.advance_chat(chat_seconds)
        if now - self.last_save >= 30:
            self.persist_progress()

    def advance_supervision(self, seconds: float) -> None:
        for event in self.supervisor.advance(seconds):
            if event == "reminder":
                self.present_reminder(False, 60000)
            elif event == "angry":
                self.pet.set_state("angry")
                self.present_reminder(True, ANGRY_BUBBLE_MS)
            elif event == "calm":
                self.pet.set_state("normal")
                self.invalidate_message()
                if self.pet.isVisible() and self.session.available:
                    self.show_bubble("哼，气消了。别以为我原谅你了。", 6000)
            elif event == "ignored":
                self.invalidate_message()
        self.update_response_status()

    def chat_eligible(self) -> bool:
        """硬条件：任一不满足就重新计时（开关关了、看不见、锁屏或正在退出）。

        刻意不要求存在未完成任务——全部做完了它也该继续陪你说话；
        也刻意不看「暂停提醒」——两个开关各管各的，陪聊有自己的按钮。
        """
        return (not self.closing
                and bool(self.config.get("chat_enabled", False))
                and self.pet.isVisible()
                and self.session.available)

    def chat_quiet(self) -> bool:
        """软条件：现在不方便开口。**只跳过发送，不重置计时**——攒着，等气泡空了补上。

        早先把「生气 / 有待回应提醒」也算进硬条件，于是宠物一生气就每个 tick 清零计时器，
        15 分钟永远攒不满；而生气会一直持续到用户点按钮为止，陪聊就此永久失效。
        """
        return self.bubble.isVisible()

    def advance_chat(self, seconds: float) -> None:
        if not self.chat_eligible():
            self.chat.reset()
            return
        if self.chat.advance(seconds) and not self.chat_quiet():
            kind = self.chat.kind()
            self.chat.consume()
            self.send_chat(kind)

    def chat_context(self) -> dict:
        try:
            rows = read_tasks()
        except (OSError, UnicodeError):
            rows = []
        pending = [task for task in rows if not task.status.startswith("已完成")]
        return build_context(
            name=self.config.get("pet_name", "Deepseek"),
            done=len(rows) - len(pending),
            left=len(pending),
            titles=[task.title for task in pending],
            minutes=self.supervisor.no_progress / 60,
            hour=datetime.now().hour,
        )

    def send_chat(self, kind: str) -> None:
        if self.closing or not self.pet.isVisible() or not self.session.available:
            return
        if self.supervisor.angry and kind != "tease":
            kind = "tease"   # 正在闹脾气的时候，说什么都像撒娇
        context = self.chat_context()
        self.request_id += 1
        self.show_bubble(local_chat_message(kind, context), 12000)
        if (self.config.get("api_enabled", False) and self.config.get("api_key")
                and not self.chat_threads and time.monotonic() - self.last_chat_api_at >= 30):
            self.last_chat_api_at = time.monotonic()
            worker = ChatWorker(kind, context, self.config)
            self.chat_threads.add(worker)
            request_id = self.request_id
            worker.result.connect(lambda text, rid=request_id: self._show_chat_result(text, rid))
            worker.finished.connect(lambda worker=worker: self._release_chat_worker(worker))
            worker.start()

    def chat_now(self) -> None:
        kind = self.chat.kind()
        self.chat.consume()
        self.chat.reset()
        self.send_chat(kind)

    def toggle_chat(self, enabled: bool) -> None:
        self.config["chat_enabled"] = enabled
        self.chat_action.setChecked(enabled)
        self.chat.reset()
        self.persist_progress()

    def welcome(self) -> None:
        if self.supervisor.active:
            self.present_reminder(False, 15000)

    def toggle_pause(self, paused: bool) -> None:
        self.tick()
        self.config["reminder_enabled"] = not paused
        self.pause_action.setChecked(paused)
        self.refresh_availability()
        self.persist_progress()
        self.update_response_status()

    def current_tasks(self) -> list[Task]:
        return [task for task in read_tasks() if not task.status.startswith("已完成")]

    def sync_progress(self) -> bool:
        try:
            if not PLAN_PATH.exists():
                raise OSError("找不到学习计划，请检查工作区位置。")
            rows = read_tasks()
        except (OSError, UnicodeError):
            self.plan_valid = False
            self.refresh_availability()
            return False
        self.plan_valid = True
        status = {task.number: task.status.startswith("已完成") for task in rows}
        completed = any(done and self.task_status.get(number) is False for number, done in status.items())
        changed = status != self.task_status
        self.tasks = [task for task in rows if not status[task.number]]
        self.task_status = status
        if completed:
            self.supervisor.complete()
            self.invalidate_message()
            self.pet.set_state("normal")
            self.last_tick = time.monotonic()
        self.refresh_availability()
        if changed or completed:
            self.persist_progress()
        if completed and self.pet.isVisible() and self.session.available:
            self.show_bubble(f"做完一项啦，表现不错！还剩 {len(self.tasks)} 项。" if self.tasks else "全部完成啦！哼，今天表现还算不错。", 8000)
        return completed

    def respond(self, minutes: int) -> None:
        if minutes not in (30, 60):
            return
        self.tick()
        self.supervisor.respond(minutes)
        self.startup_timer.stop()
        self.invalidate_message()
        self.pet.set_state("normal")
        self.persist_progress()
        self.update_response_status()
        if self.pet.isVisible() and self.session.available:
            self.show_bubble("好啦，我等你。60 分钟后再来看。" if minutes == 60 else "那就再等你 30 分钟，哼。", 6000)

    def update_response_status(self) -> None:
        if self.dialog is None:
            return
        s = self.supervisor
        if not self.tasks:
            text = "全部完成"
        elif not s.active:
            text = "提醒已暂停"
        elif s.angry:
            text = "等待你的回应"
        elif s.response_left is not None:
            text = f"等待回应 · {math.ceil(s.response_left)} 秒"
        else:
            text = f"下次提醒 · {math.ceil(s.next_reminder / 60)} 分钟"
        self.dialog.response_status.setText(text)
        self.dialog.working.setEnabled(bool(self.tasks))
        self.dialog.later.setEnabled(bool(self.tasks))

    def remind(self) -> None:
        # Manual inspection never creates or clears an ignored-reminder deadline.
        self.sync_progress()
        if not self.plan_valid:
            self.show_bubble("无法读取学习计划，请检查文件。")
        elif self.supervisor.angry:
            self.present_reminder(True, ANGRY_BUBBLE_MS)
        else:
            duration = max(1, int(self.supervisor.response_left * 1000)) if self.supervisor.response_left is not None else 15000
            self.present_reminder(False, duration)

    def present_reminder(self, urgent: bool, duration: int) -> None:
        self.request_id += 1
        tasks = self.tasks
        self.show_bubble(local_message(tasks, self.config, urgent), duration, controls=bool(tasks))
        if self.config.get("api_enabled", False) and self.config.get("api_key") and not self.api_threads and time.monotonic()-self.last_api_at>=30:
            self.last_api_at = time.monotonic()
            worker = ApiWorker(tasks, self.config, urgent)
            self.api_threads.add(worker)
            request_id = self.request_id
            worker.result.connect(lambda text, rid=request_id: self._show_api_result(text,rid))
            worker.finished.connect(lambda worker=worker: self._release_api_worker(worker))
            worker.start()

    def _show_api_result(self, text: object, request_id: int) -> None:
        if not self.closing and request_id==self.request_id and self.pet.isVisible() and self.bubble.isVisible() and isinstance(text,str) and text.strip():
            self.bubble.update_text(text.strip()[:250])
            self.follow_bubble()

    def _release_api_worker(self, worker: ApiWorker) -> None:
        self.api_threads.discard(worker)
        worker.deleteLater()

    def _show_chat_result(self, text: object, request_id: int) -> None:
        # 与提醒同一套“请求已过期就丢弃”的规则：气泡被换掉或关掉，迟到的文案不再覆盖。
        if not self.closing and request_id==self.request_id and self.pet.isVisible() and self.bubble.isVisible() and isinstance(text,str) and text.strip():
            self.bubble.update_text(text.strip()[:250])
            self.follow_bubble()

    def _release_chat_worker(self, worker: ChatWorker) -> None:
        self.chat_threads.discard(worker)
        worker.deleteLater()

    def stop_workers(self) -> None:
        if self.closing:
            return
        self.tick()
        self.closing = True
        self.tick_timer.stop()
        self.startup_timer.stop()
        self.supervisor.suspend()
        self.persist_progress()
        self.invalidate_message()
        self.session.close()
        self.pet.hide()
        self.tray.hide()
        for worker in list(self.api_threads):
            worker.wait()
        self.api_threads.clear()
        for worker in list(self.chat_threads):
            worker.wait()
        self.chat_threads.clear()

    def follow_bubble(self) -> None:
        if self.bubble.isVisible():
            self.bubble.follow(self.pet.mapToGlobal(QPoint(self.pet.width(),24)))

    def save_position(self) -> None:
        self.config["position"] = [self.pet.x(),self.pet.y()]
        save_config(self.config)

    def toggle_visible(self, visible: bool) -> None:
        self.tick()
        self.pet.setVisible(visible)
        self.visible_action.setChecked(visible)
        self.refresh_availability()
        self.persist_progress()

    def show_bubble(self, text: str, duration: int = 12000, controls: bool = False) -> None:
        anchor = self.pet.mapToGlobal(QPoint(self.pet.width(), 24))
        self.bubble.show_message(text, anchor, duration, controls)

    def open_tasks(self) -> None:
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        try:
            if not PLAN_PATH.exists():
                raise OSError("找不到学习计划，请检查工作区位置。")
            tasks = read_tasks()
        except (OSError, UnicodeError) as error:
            self.show_bubble(str(error))
            return
        self.sync_progress()
        self.invalidate_message()
        self.dialog = TaskDialog(tasks, self.config.copy(), self.save_tasks, self.pet)
        self.dialog.remind_now.connect(lambda: self.remind())
        self.dialog.responded.connect(self.respond)
        self.dialog.finished.connect(self._clear_dialog)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.update_response_status()

    def _clear_dialog(self, result: int) -> None:
        self.dialog.deleteLater()
        self.dialog = None

    def save_tasks(self, selected: set[str]) -> None:
        self.sync_progress()
        complete_tasks(selected)
        was_enabled = self.config["reminder_enabled"]
        was_chat = bool(self.config.get("chat_enabled", False))
        if self.dialog:
            for name in ("reminder_enabled","chat_enabled","api_enabled","startup_enabled"):
                self.config[name] = self.dialog.config[name]
        if self.config["startup_enabled"] != startup_enabled():
            self.toggle_startup(self.config["startup_enabled"])
        self.sync_progress()
        self.pause_action.setChecked(not self.config["reminder_enabled"])
        self.chat_action.setChecked(bool(self.config.get("chat_enabled", False)))
        if was_chat != bool(self.config.get("chat_enabled", False)):
            self.chat.reset()
        if was_enabled != self.config["reminder_enabled"]:
            self.refresh_availability()
        if not self.config.get("api_enabled"):
            self.request_id += 1
        self.persist_progress()

    def show_context_menu(self, point: QPoint) -> None:
        self.tray.contextMenu().popup(point)

    def toggle_startup(self, enabled: bool) -> None:
        try:
            set_startup(enabled)
            self.config["startup_enabled"] = enabled
            save_config(self.config)
            self.startup_action.setChecked(enabled)
        except OSError as error:
            self.startup_action.setChecked(not enabled)
            self.config["startup_enabled"] = startup_enabled()
            QMessageBox.warning(self.pet, "开机启动", f"设置失败：{error}")

    def show_config_location(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(APP_DIR)))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DeepSeek Desktop Pet")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setQuitOnLastWindowClosed(False)
    lock = QLockFile(str(APP_DIR / "pet.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        return 0
    controller = DesktopPet()
    controller.start()
    if "--tasks" in sys.argv:
        QTimer.singleShot(400,controller.open_tasks)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
