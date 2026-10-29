"""Qt desktop control panel. All data-plane input handling runs off the GUI thread."""
import argparse
import atexit
import ctypes
import os
import platform
import socket
import sys
import time

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QMenu,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
)

from . import __version__
from .branding import app_icon, line_icon, tray_icon
from .clipboard import ClipboardSync
from .config import Settings
from .drop_overlay import EdgePortal
from .engine import Engine
from .file_clipboard import FileClipboard
from .i18n import exception_text, tr
from .i18n_widgets import (
    QAction,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSystemTrayIcon,
    QWidget,
)
from .input import create_backend
from .instance import START_TIMEOUT, PROBE_TIMEOUT, claim, discard, forward, lock_file, owner, release
from .layout_ui import LayoutEditor
from .network import Discovery, Network
from .permissions import PermissionGuide
from .security import Identity
from .share_ui import SharePanel
from .sharing import SharingService
from .ui import (
    ACCENT,
    INK_2,
    WARN_INK,
    PeerCard,
    ScreenMap,
    StatusBadge,
    apply_theme,
    card,
    keycaps,
    text_label,
)

OS_NAMES = {'Darwin': 'macOS', 'Windows': 'Windows', 'Linux': 'Linux'}


def shortcut():
    """The one global hotkey: take keyboard and mouse back from the peer."""
    return 'Ctrl+Alt+Shift+Esc' if sys.platform.startswith('linux') else 'Ctrl+Alt+Esc'


def set_dock_icon(visible):
    """Show or hide the Dock tile. Menu bar access is unaffected; no-op off macOS."""
    if sys.platform != 'darwin':
        return
    try:
        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                            NSApplicationActivationPolicyRegular)
        policy = NSApplicationActivationPolicyRegular if visible else NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(policy)
    except (ImportError, AttributeError, ValueError):
        pass


def activate_app():
    """Accessory apps must ask the window server to come forward explicitly."""
    if sys.platform != 'darwin':
        return
    try:
        from AppKit import NSApplication
        application = NSApplication.sharedApplication()
        activate = getattr(application, 'activateIgnoringOtherApps_', None)
        if activate:
            activate(True)
    except (ImportError, AttributeError):
        pass


class Bridge(QObject):
    pair = Signal(object)
    peer = Signal(dict)
    # QVariant dict/str signals coerce Message subclasses to plain strings.
    # Python object signals preserve sources/parameters across queued delivery.
    status = Signal(object)
    error = Signal(object)
    sharing = Signal(object)
    auxiliary = Signal(object)



class MainWindow(QMainWindow):
    def __init__(self, settings, identity, demo=False):
        super().__init__()
        self.settings, self.identity, self.demo = settings, identity, demo
        self.peers, self.dialogs = {}, {}
        self.closed = False
        self.permission_guide = None
        self.bridge = Bridge()
        self.bridge.peer.connect(self.found_peer)
        self.bridge.pair.connect(self.pair_dialog)
        self.bridge.status.connect(self.show_status)
        self.bridge.error.connect(self.notice)
        self.engine = Engine(settings, self.bridge.status.emit, local_id=identity.id,
                             auxiliary=self.bridge.auxiliary.emit)
        self.bridge.auxiliary.connect(self.engine_event)
        self.backend = create_backend(self.engine.enqueue)
        self.engine.backend = self.backend
        self.network = Network(identity, settings, self.bridge.pair.emit,
                               lambda s: self.engine.enqueue({"type": "connected", "session": s}),
                               lambda s, m: self.engine.enqueue({"type": "network", "session": s, "message": m}),
                               self.disconnected, self.bridge.error.emit)
        self.network.screen_provider = self.backend.screen_info
        self.discovery = Discovery(identity, settings, self.bridge.peer.emit, self.bridge.error.emit)
        self.sharing = SharingService(self.network, settings, self.sharing_allowed, self.bridge.sharing.emit)
        self.bridge.sharing.connect(self.sharing_event)
        self.setWindowTitle(f"MouseShare · {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(880, 700)
        self.setMinimumSize(780, 620)
        self.build_ui()
        self.bind_settings_feedback()
        self.build_tray()
        self.clipboard_sync = None if demo else ClipboardSync(
            self.sharing, settings, self.share_panel.status.setText, self)
        self.file_clipboard = None if demo else FileClipboard(self)
        self.edge_portal = EdgePortal(self)
        self.refresh_layout_devices()
        self.linux_session = None
        if sys.platform.startswith('linux') and not demo:
            from .linux_session import LinuxSession
            self.linux_session = LinuxSession(self.backend, self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_peers)
        self.timer.timeout.connect(self.save_diagnostics)
        self.timer.start(2000)
        if not demo:
            self.backend.start()
            self.engine.start()
            try:
                self.network.start()
                self.discovery.start()
            except OSError:
                self.notice(tr('共享端口 24872 被占用。请检查是否运行了另一份 MouseShare。'))
        else:
            self.show_status({"state": "local", "paused": False, "connected": False, "peer_available": False,
                              "peer_name": "", "ready": False, "error": tr('演示界面 · 输入共享未启动')})

    def build_ui(self):
        shell = QWidget()
        shell.setObjectName("shell")
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(shell)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(188)
        rail = QVBoxLayout(sidebar)
        rail.setContentsMargins(12, 20, 12, 14)
        rail.setSpacing(4)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 0)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(36, 36))
        brand.addWidget(logo)
        brand.addWidget(text_label("MouseShare", "brand"), 1)
        brand.setSpacing(6)
        rail.addLayout(brand)
        rail.addSpacing(18)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.stack = QStackedWidget()
        self.stack.setObjectName("pages")
        for index, (label, icon) in enumerate([(tr('设备连接'), "devices"), (tr('共享设置'), "settings"),
                                                (tr('文件与剪贴板'), "devices"), (tr('屏幕布局'), "devices")]):
            button = QPushButton(label)
            button.setObjectName("nav")
            button.setIcon(line_icon(icon, INK_2, checked=ACCENT))
            button.setIconSize(QSize(19, 19))
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self.stack.setCurrentIndex(i))
            self.nav_group.addButton(button, index)
            rail.addWidget(button)
        self.nav_group.button(0).setChecked(True)
        rail.addStretch()
        self.permission_button = QPushButton(tr('系统权限'))
        self.permission_button.setObjectName("quiet")
        self.permission_button.setIcon(line_icon("shield", INK_2, 17))
        self.permission_button.clicked.connect(self.check_permissions)
        rail.addWidget(self.permission_button)
        rail.addSpacing(14)
        footer = QVBoxLayout()
        footer.setContentsMargins(6, 0, 0, 0)
        footer.setSpacing(4)
        footer.addWidget(text_label(f"VERSION {__version__}", "eyebrow"))
        footer.addWidget(text_label(tr('让桌面上的电脑连在一起。'), "hint", True))
        rail.addLayout(footer)
        outer.addWidget(sidebar)
        outer.addWidget(self.stack, 1)
        self.build_home()
        self.build_settings()
        layout, _ = self.make_page(tr('文件与剪贴板'), tr('复制、拖放，把内容送到另一台电脑。'))
        self.share_panel = SharePanel(self)
        layout.addWidget(self.share_panel)
        layout.addStretch()
        layout, _ = self.make_page(tr('屏幕布局'), tr('拖动屏幕，排列你的整张桌面。最多连接 8 台电脑。'))
        self.layout_editor = LayoutEditor(self.settings, self.identity.id,
                                          lambda value: self.engine.command("layout", layout=value),
                                          self.backend.apply_arrangement)
        layout.addWidget(self.layout_editor)
        layout.addStretch()
        self.stack.currentChanged.connect(lambda i: self.nav_group.button(i).setChecked(True))

    def engine_event(self, event):
        if self.closed:
            return
        if event['type'] == 'layout_applied':
            self.layout_editor.applied(event['layout'])
        if getattr(self, 'file_clipboard', None):
            self.file_clipboard.handle_event(event)
        if event['type'] == 'peer_connected':
            self.refresh_peers()
        elif event['type'] == 'screens_changed':
            self.refresh_layout_devices()

    def refresh_layout_devices(self):
        if not hasattr(self, 'layout_editor'):
            return
        metadata = {self.identity.id: dict(name=self.settings.get('name'), connected=True,
                                          screen=self.backend.screen_info())}
        for key, peer in self.settings.get('peers').items():
            session = self.network.peer_session(key)
            metadata[key] = dict(name=peer.get('name', key[:8]), connected=bool(session),
                                 screen=getattr(session, 'peer_screen', {}))
        self.layout_editor.update_devices(metadata)
        if hasattr(self, 'share_panel'):
            self.share_panel.update_recipients()

    def sharing_allowed(self, kind):
        session = self.network.session
        return bool(session and session.connected and "sharing-v1" in getattr(session, "peer_features", [])
                    and not self.engine.paused and not self.engine.system_blocked)

    def sharing_event(self, event):
        if self.closed:
            return
        if event['type'] == 'transfer':
            self.share_panel.transfer(event)
            if getattr(self, 'file_clipboard', None):
                self.file_clipboard.transfer(event)
        elif event['type'] == 'clipboard' and self.clipboard_sync:
            if event.get('control') is self.network.session:
                self.clipboard_sync.apply(event['payload'])
        elif event['type'] == 'clipboard_status':
            self.share_panel.status.setText(event['text'])

    def show_sharing(self):
        self.stack.setCurrentIndex(2)
        self.show_normal()

    def make_page(self, title, subtitle):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root = QWidget()
        root.setObjectName("page")
        scroll.setWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 4)
        labels = QVBoxLayout()
        labels.setSpacing(4)
        labels.addWidget(text_label(title, "title"))
        labels.addWidget(text_label(subtitle, "subtitle", True))
        heading.addLayout(labels, 1)
        layout.addLayout(heading)
        wrapper = QWidget()
        wrapper.setObjectName("page")
        page_layout = QVBoxLayout(wrapper)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        page_layout.addWidget(scroll, 1)
        self.page_layouts = getattr(self, "page_layouts", {})
        self.page_layouts[layout] = page_layout
        self.stack.addWidget(wrapper)
        return layout, heading

    def build_home(self):
        layout, heading = self.make_page(tr('设备连接'), tr('一套键鼠，在多台电脑间自由切换。'))
        self.status_badge = StatusBadge(tr('正在启动'))
        heading.addWidget(self.status_badge, 0, Qt.AlignmentFlag.AlignTop)
        box, body = card()
        box.setObjectName("canvas")
        body.setContentsMargins(20, 18, 20, 14)
        body.setSpacing(4)
        self.state_label = text_label(tr('正在启动…'), "state")
        self.detail = text_label(tr('准备连接你的另一台电脑。'), "hint", True)
        body.addWidget(self.state_label)
        body.addWidget(self.detail)
        self.screen_map = ScreenMap()
        body.addWidget(self.screen_map)
        self.edge_hint = text_label("", "chip")
        edge_row = QHBoxLayout()
        edge_row.addStretch()
        edge_row.addWidget(self.edge_hint)
        self.permission_cta = QPushButton(tr('设置键鼠权限'))
        self.permission_cta.setObjectName("primary")
        self.permission_cta.clicked.connect(self.check_permissions)
        edge_row.addWidget(self.permission_cta)
        edge_row.addStretch()
        body.addLayout(edge_row)
        layout.addWidget(box)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.pause_button = QPushButton(tr('暂停共享'))
        self.pause_button.setIcon(line_icon("pause", INK_2, 16))
        self.pause_button.clicked.connect(lambda: self.engine.command("pause"))
        row.addWidget(self.pause_button, 1)
        take_back = QPushButton(tr('收回控制'))
        take_back.setIcon(line_icon("return", INK_2, 16))
        take_back.setToolTip(tr('立即释放远端按键并恢复本机控制。') + shortcut())
        take_back.clicked.connect(lambda: self.engine.command("panic"))
        row.addWidget(take_back, 1)
        layout.addLayout(row)
        box, body = card()
        body.setContentsMargins(20, 18, 20, 16)
        body.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(text_label(tr('附近的设备'), "section"))
        top.addStretch()
        self.discovery_label = text_label(tr('自动发现已开启'), "hint")
        top.addWidget(self.discovery_label)
        body.addLayout(top)
        body.addSpacing(4)
        self.peer_buttons = {}
        self.peer_group = QButtonGroup(self)
        self.peer_group.setExclusive(True)
        self.peer_group.buttonClicked.connect(self.update_actions)
        self.peer_list = QVBoxLayout()
        self.peer_list.setSpacing(8)
        body.addLayout(self.peer_list)
        self.empty_label = text_label(tr('在另一台电脑上打开 MouseShare，设备会自动出现在这里。'), "hint", True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setContentsMargins(4, 22, 4, 22)
        body.addWidget(self.empty_label)
        body.addSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.connect_button = QPushButton(tr('配对并连接'))
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(self.connect_selected)
        self.control_button = QPushButton(tr('切换控制到此电脑'))
        self.control_button.setToolTip(tr('无需移动触控板；Ctrl+Alt+空格切换，再按一次收回。'))
        self.control_button.clicked.connect(lambda: self.engine.command('switch_peer', peer=self.selected_id()))
        row.addWidget(self.control_button)
        row.addWidget(self.connect_button)
        self.forget_button = QPushButton(tr('取消配对'))
        self.forget_button.clicked.connect(self.forget_selected)
        row.addWidget(self.forget_button)
        row.addStretch()
        manual = QPushButton(tr('通过 IP 连接'))
        manual.setObjectName("link")
        manual.setCheckable(True)
        row.addWidget(manual)
        body.addLayout(row)
        self.manual_row = QWidget()
        ip_row = QHBoxLayout(self.manual_row)
        ip_row.setContentsMargins(0, 6, 0, 0)
        self.manual_ip = QLineEdit()
        self.manual_ip.setPlaceholderText(tr('对方的 IPv4 地址，例如 192.168.1.20'))
        self.manual_ip.returnPressed.connect(self.connect_ip)
        ip_row.addWidget(self.manual_ip)
        connect_ip = QPushButton(tr('连接'))
        connect_ip.clicked.connect(self.connect_ip)
        ip_row.addWidget(connect_ip)
        body.addWidget(self.manual_row)
        self.manual_row.hide()
        manual.toggled.connect(self.manual_row.setVisible)
        layout.addWidget(box)
        self.notice_label = text_label(tr('自动发现同一局域网内的设备，配对后加密连接。'), "hint", True)
        layout.addWidget(self.notice_label)
        layout.addStretch()
        self.update_map()
        self.update_actions()

    def build_settings(self):
        layout, _ = self.make_page(tr('共享设置'), tr('让跨屏方式适合你的桌面摆放和使用习惯。'))
        box, body = card(tr('屏幕位置'), tr('在屏幕布局中拖动电脑，可设置错位、多方向与多台设备。'))
        arrange = QPushButton(tr('打开可拖动的屏幕布局'))
        arrange.setObjectName("primary")
        arrange.clicked.connect(lambda: self.stack.setCurrentIndex(3))
        body.addWidget(arrange)
        form = QFormLayout()
        form.setSpacing(18)
        self.side = QComboBox()
        for label, value in [(tr('右侧 →'), "right"), (tr('左侧 ←'), "left"), (tr('上方 ↑'), "top"), (tr('下方 ↓'), "bottom")]:
            self.side.addItem(label, value)
        self.side.setCurrentIndex(self.side.findData(self.settings.get("side")))
        form.addRow(tr('简单双机方向（布局关闭时）'), self.side)
        form.addRow(tr('跨屏方式'), text_label(tr('碰到相接边缘立即切换'), 'hint', True))
        row = QHBoxLayout()
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.25, 4)
        self.speed.setSingleStep(0.25)
        self.speed.setSuffix(" ×")
        self.speed.setValue(self.settings.get("sensitivity"))
        row.addWidget(self.speed)
        row.addWidget(text_label(tr('调整本机接收的鼠标速度'), "hint"), 1)
        form.addRow(tr('鼠标速度'), row)
        body.addLayout(form)
        layout.addWidget(box)
        box, body = card(tr('本机偏好'))
        form = QFormLayout()
        form.setSpacing(16)
        self.device_name = QLineEdit(self.settings.get("name"))
        self.device_name.setMaxLength(80)
        form.addRow(tr('设备名称'), self.device_name)
        self.swap = QCheckBox(tr('交换 Ctrl 与 ⌘ / Win'))
        self.swap.setChecked(self.settings.get("swap_ctrl_meta", False))
        self.swap.setToolTip(tr('接收远端输入时，沿用另一台电脑的复制粘贴修饰键习惯。'))
        form.addRow(tr('接收时的修饰键'), self.swap)
        self.startup = QCheckBox(tr('登录后自动启动'))
        self.startup.setChecked(self.settings.get("startup"))
        form.addRow(tr('开机启动'), self.startup)
        self.allow_locked = QCheckBox(tr('锁屏后仍允许交接'))
        self.allow_locked.setChecked(self.settings.get("allow_locked", False))
        self.allow_locked.setToolTip(tr('本机锁定或停在安全桌面时，仍接受其他电脑的控制。用于在锁屏界面输入密码，最终能否输入取决于系统限制。'))
        form.addRow(tr('锁屏交接'), self.allow_locked)
        self.dock_icon = QCheckBox(tr('在程序坞显示图标'))
        self.dock_icon.setChecked(self.settings.get('dock_icon', False))
        self.dock_icon.setToolTip(tr('关闭后 MouseShare 只留在菜单栏，不占程序坞，也不会出现在 ⌘Tab 切换里。点菜单栏图标即可打开窗口。'))
        self.dock_icon.toggled.connect(set_dock_icon)
        if sys.platform == 'darwin':
            form.addRow(tr('程序坞图标'), self.dock_icon)
        body.addLayout(form)
        self.device_info = text_label(tr('设备指纹  {p0}', p0=self.identity.id[:16]), "hint")
        body.addWidget(self.device_info)
        layout.addWidget(box)
        box, body = card(tr('随时收回控制'))
        body.setSpacing(10)
        self.reclaim = QCheckBox(tr('操作本机实体键鼠立即收回'))
        self.reclaim.setChecked(self.settings.get("reclaim_on_input", True))
        self.reclaim.setToolTip(tr('被其他电脑控制时，动一下本机的键鼠就把控制收回来。关闭后只能用下方快捷键或托盘菜单收回；单独按住 Control / Shift 之类的修饰键始终不会收回。'))
        body.addWidget(self.reclaim)
        modifiers = ["Control", "Option"] if sys.platform == "darwin" else ["Ctrl", "Alt"]
        if sys.platform.startswith('linux'):
            modifiers.append('Shift')
        body.addWidget(keycaps([*modifiers, "Esc"], tr('立即收回键鼠')))
        if sys.platform == "darwin":
            body.addWidget(text_label(tr('部分 Mac 键盘需要同时按 Fn。'), "hint"))
        layout.addWidget(box)
        self.add_save_row(layout)
        layout.addStretch()

    def add_save_row(self, layout):
        row = QHBoxLayout()
        row.setSpacing(12)
        save = QPushButton(tr('保存设置'))
        save.setObjectName("primary")
        save.setMinimumWidth(108)
        save.clicked.connect(self.save_settings)
        self.save_buttons = getattr(self, "save_buttons", [])
        self.save_buttons.append(save)
        row.addWidget(save)
        self.saved_labels = getattr(self, "saved_labels", [])
        hint = text_label(tr('更改后保存生效'), "hint", True)
        self.saved_labels.append(hint)
        row.addWidget(hint, 1)
        footer = QFrame()
        footer.setObjectName("footer")
        row.setContentsMargins(24, 10, 24, 12)
        footer.setLayout(row)
        self.page_layouts[layout].addWidget(footer)

    def form_values(self):
        return dict(name=self.device_name.text().strip(), side=self.side.currentData(),
                    sensitivity=self.speed.value(),
                    startup=self.startup.isChecked(), swap_ctrl_meta=self.swap.isChecked(),
                    allow_locked=self.allow_locked.isChecked(), dock_icon=self.dock_icon.isChecked(),
                    reclaim_on_input=self.reclaim.isChecked())

    def bind_settings_feedback(self):
        self.saved_form = self.form_values()
        self.form_saved_once = False
        for signal in (self.device_name.textChanged, self.side.currentIndexChanged,
                       self.speed.valueChanged,
                       self.startup.toggled, self.allow_locked.toggled, self.dock_icon.toggled,
                       self.reclaim.toggled, self.swap.toggled):
            signal.connect(self.settings_changed)
        self.settings_changed()

    def save_feedback(self, text, warning=False):
        for label in self.saved_labels:
            label.setText(text)
            label.setProperty("tone", "warning" if warning else "")
            label.style().unpolish(label)
            label.style().polish(label)

    def settings_changed(self, *args):
        dirty = self.form_values() != self.saved_form
        for button in self.save_buttons:
            button.setEnabled(dirty)
        self.save_feedback(tr('有未保存的更改') if dirty else
                           tr('所有更改已保存') if self.form_saved_once else tr('更改后点击保存生效'), dirty)

    def update_actions(self):
        if not hasattr(self, "connect_button"):
            return
        selected = self.selected_id()
        session = self.network.peer_session(selected) if selected else None
        self.connect_button.setEnabled(bool(selected))
        self.control_button.setEnabled(bool(session and session.connected))
        self.connect_button.setText(tr('选择内容共享目标') if session and session.connected else tr('连接中…') if session else tr('配对并连接'))
        self.forget_button.setEnabled(selected in self.settings.get("peers"))

    def update_map(self):
        state = getattr(self, "ui_state", {})
        peer_name = state.get("peer_name") or tr('等待另一台电脑')
        peer_os = ""
        if state.get("peer_name"):
            peer = next((v for v in self.peers.values() if v.get("name") == peer_name), {})
            peer_os = OS_NAMES.get(peer.get("os"), "")
        self.screen_map.configure(side=self.settings.get("side"), local_name=self.settings.get("name"),
                                  local_os=OS_NAMES.get(platform.system(), platform.system()),
                                  peer_name=peer_name, peer_os=peer_os, connected=bool(state.get("connected")),
                                  blocked=bool(state.get("paused") or state.get("system_blocked")
                                               or not state.get("ready")
                                               or state.get("connected") and not state.get("peer_available")),
                                  owner=state.get("state", "local"))
        direction = {"left": tr('左侧'), "right": tr('右侧'), "top": tr('上方'), "bottom": tr('下方')}[self.settings.get("side")]
        if not state.get("ready"):
            hint = tr('完成权限设置与输入检查后即可跨屏')
        elif state.get("system_blocked"):
            hint = tr('系统恢复桌面后可继续共享')
        elif state.get("paused"):
            hint = tr('共享已暂停，点击「恢复共享」继续')
        elif not state.get("connected"):
            hint = tr('连接另一台电脑后即可跨屏')
        elif not state.get("peer_available"):
            hint = tr('对方暂不可用，请在对方电脑检查共享状态')
        elif state.get("state") == "sending":
            hint = tr('从对方屏幕原路返回即可收回')
        elif state.get("state") == "receiving":
            hint = tr('操作本机实体键鼠可立即收回')
        elif self.engine.layout():
            hint = tr('按屏幕布局，从相接的边缘跨屏；离线设备处会停止')
        else:
            hint = tr('移到{p0}边缘立即跨屏', p0=direction)
        self.edge_hint.setText(hint)
        self.permission_cta.setVisible(not state.get("ready"))

    def build_tray(self):
        self.tray = QSystemTrayIcon(tray_icon(sys.platform == "darwin"), self)
        self.tray.setToolTip("MouseShare")
        menu = QMenu()
        show = QAction(tr('打开 MouseShare'), self)
        show.triggered.connect(self.show_normal)
        menu.addAction(show)
        release = QAction(tr('立即收回键鼠 · ') + shortcut(), self)
        release.triggered.connect(lambda: self.engine.command("panic"))
        menu.addAction(release)
        share = QAction(tr('文件与剪贴板'), self)
        share.triggered.connect(self.show_sharing)
        menu.addAction(share)
        self.tray.messageClicked.connect(self.show_sharing)
        menu.addSeparator()
        quit_action = QAction(tr('退出'), self)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda why: self.show_normal()
                                   if why == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def show_normal(self):
        self.show()
        self.raise_()
        self.activateWindow()
        activate_app()

    def notice(self, text):
        self.notice_label.setText(text)

    def show_status(self, state):
        if state.get('active_peer'):
            self.network.select(state['active_peer'])
        previous_state = getattr(self, 'ui_state', {})
        self.ui_state = state
        need_permission = (not state["ready"] and sys.platform == "darwin" and
                           (self.demo or not all(self.backend.permission_status().values())))
        self.pause_button.setText(tr('恢复共享') if state["paused"] else tr('暂停共享'))
        self.pause_button.setIcon(line_icon("play" if state["paused"] else "pause", INK_2, 16))
        peer = state["peer_name"]
        if not state["ready"]:
            title = tr('需要开启键鼠权限') if need_permission else tr('输入服务未就绪')
            detail = (tr('点击下方按钮，按步骤开启辅助功能与输入监控。') if need_permission else
                      state["error"] or tr('点击下方按钮检查输入服务。'))
        elif state.get("system_blocked"):
            title, detail = tr('共享已安全暂停'), tr('系统处于锁屏或其他非交互桌面；恢复桌面后可继续使用。')
        elif state["paused"]:
            title, detail = tr('共享已暂停'), state["error"] or tr('点击恢复共享继续。')
        elif state["state"] == "sending":
            title, detail = tr('正在控制另一台电脑'), tr('键鼠输出到 {p0}。从对方屏幕原路返回即可收回。', p0=peer)
        elif state["state"] == "receiving":
            title, detail = tr('正在接收远端键鼠'), tr('由 {p0} 控制。操作本机实体键鼠可立即收回。', p0=peer)
        elif state["state"] == "standby":
            title, detail = tr('控制已交接到下一台电脑'), tr('本机光标已隐藏。操作实体键鼠或按收回快捷键可恢复。')
        elif state["connected"]:
            title = tr('已连接 · 本机控制中')
            detail = tr('已连接 {p0}。', p0=peer) + (tr('把鼠标移到相接边缘即可立即跨屏。') if state["peer_available"]
                                         else tr('对方已暂停共享或等待授权。'))
        else:
            title, detail = tr('等待连接'), tr('启动另一台电脑上的 MouseShare，然后在下方选择电脑进行配对。')
        self.state_label.setText(title)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setText(detail)
        badge = (tr('需要授权') if need_permission else tr('需要检查'), "warning") if not state["ready"] else (
            (tr('已暂停'), "idle")
            if state["paused"] or state.get("system_blocked") else (tr('已连接'), "active")
            if state["connected"] else (tr('等待连接'), "idle"))
        self.status_badge.set_status(*badge)
        self.permission_button.setText(tr('系统权限 · 已就绪') if state["ready"] else
                                       tr('系统权限 · 待授权') if need_permission else tr('输入服务 · 待检查'))
        self.permission_cta.setText(tr('设置键鼠权限') if need_permission else tr('检查输入服务'))
        if self.permission_button.property("tone") != ("" if state["ready"] else "warning"):
            self.permission_button.setProperty("tone", "" if state["ready"] else "warning")
            self.permission_button.setIcon(line_icon("shield", INK_2 if state["ready"] else WARN_INK, 17))
            self.permission_button.style().unpolish(self.permission_button)
            self.permission_button.style().polish(self.permission_button)
        self.permission_button.setToolTip(state["error"] or tr('检查辅助功能和输入监控权限'))
        self.update_map()
        self.update_actions()
        self.share_panel.update_recipients()
        self.tray.setToolTip(tr('MouseShare · {title}', title=title))
        if state.get('paused') or state.get('system_blocked') or not state['connected']:
            self.sharing.pause()
        if hasattr(self, 'share_panel'):
            self.share_panel.drop.setEnabled(self.sharing_allowed('files') and self.settings.get('file_sharing', True))
            session = self.network.session
            if not state['connected']:
                self.share_panel.status.setText(tr('连接另一台电脑后即可共享文件与剪贴板'))
            elif session and 'sharing-v1' not in getattr(session, 'peer_features', []):
                self.share_panel.status.setText(tr('文件与剪贴板需要两端均升级至 v0.3.0 或更新版本'))
            elif state.get('paused') or state.get('system_blocked'):
                self.share_panel.status.setText(tr('内容共享已暂停；恢复共享后重新复制／发送'))
            elif not previous_state.get('connected'):
                self.share_panel.status.setText(tr('内容共享已就绪，重新复制要同步的内容'))
                if getattr(self, 'clipboard_sync', None):
                    self.clipboard_sync.poll()

    def found_peer(self, peer):
        if len(self.peers) < 64 or peer["id"] in self.peers:
            peer["seen"] = time.monotonic()
            self.peers[peer["id"]] = peer
        # Deterministic dialer prevents simultaneous automatic connection attempts.
        if (peer["id"] in self.settings.get("peers") and self.identity.id < peer["id"]
                and not self.network.peer_session(peer["id"]) and not self.demo):
            self.network.connect(peer["host"], peer["port"], peer["id"])
        self.refresh_peers()

    def refresh_peers(self):
        selected = self.selected_id()
        trusted = self.settings.get("peers")
        rows = {key: dict(value, id=key, os="", seen=0) for key, value in trusted.items()}
        rows.update({key: peer for key, peer in self.peers.items()
                     if time.monotonic() - peer["seen"] < 8})
        # Native buttons avoid Qt's Cocoa table selected-children bridge crash.
        # Keep them stable through periodic discovery updates and retain selection.
        for peer_id in list(self.peer_buttons):
            if peer_id not in rows:
                button = self.peer_buttons.pop(peer_id)
                self.peer_group.removeButton(button)
                self.peer_list.removeWidget(button)
                button.hide()
                button.deleteLater()
        for peer_id, peer in rows.items():
            button = self.peer_buttons.get(peer_id)
            if button is None:
                button = PeerCard()
                self.peer_buttons[peer_id] = button
                self.peer_group.addButton(button)
                self.peer_list.addWidget(button)
            status, tone = (tr('已配对'), "paired") if peer_id in trusted else (tr('待配对'), "idle")
            session = self.network.peer_session(peer_id)
            if session:
                status, tone = (tr('已连接'), "active") if session.connected else (tr('连接中'), "warning")
            elif not peer.get("seen"):
                status, tone = tr('离线 · 已配对'), "idle"
            os_name = OS_NAMES.get(peer.get("os"), tr('电脑'))
            name = peer.get("name", tr('电脑'))
            button.show_peer(name, os_name, peer.get("host", ""), status, tone)
            full_label = f"{name}，{os_name}，{peer.get('host', '')}，{status}"
            button.setAccessibleName(full_label)
            button.setToolTip(full_label)
            if peer_id == selected:
                button.setChecked(True)
        self.refresh_layout_devices()
        self.empty_label.setVisible(not rows)
        if rows and not self.peer_group.checkedButton():
            next(iter(self.peer_buttons.values())).setChecked(True)
        self.discovery_label.setText(tr('{p0} 台设备 · 自动发现', p0=len(rows)) if rows else tr('正在自动发现…'))
        self.update_map()
        self.update_actions()
        for key in list(self.dialogs):
            if self.dialogs[key][1].stopped.is_set():
                self.dialogs[key][0].reject()

    def selected_id(self):
        return next((peer_id for peer_id, button in getattr(self, "peer_buttons", {}).items()
                     if button.isChecked()), None)

    def save_diagnostics(self):
        # Opt-in local health snapshot, without keys, pointer paths or peer IDs.
        if self.demo or not (getattr(self, 'diagnostics_requested', False) or self.settings.get("diagnostics", False)):
            return
        import json
        report = {"version": __version__, "state": self.engine.state,
                  "captured": self.backend.captured, "ready": self.backend.ready(),
                  "error": self.backend.error,
                  "captured_moves": getattr(self.backend, "captured_moves", 0),
                  "cursor_shield": bool(getattr(getattr(self.backend, "_cursor_shield", None), "panels", [])),
                  "cursor_shield_error": getattr(getattr(self.backend, "_cursor_shield", None), "detail", "")}
        import time
        report.update(self.engine.health(), sampled_at=time.time(), recent_states=list(self.engine.health_transitions))
        path = self.settings.directory / "input-health.json"
        try:
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(report), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            pass

    def connect_selected(self):
        peer_id = self.selected_id()
        if not peer_id:
            self.notice(tr('请先在列表中选择一台电脑。'))
            return
        peer = self.peers.get(peer_id) or self.settings.get("peers").get(peer_id)
        if self.network.peer_session(peer_id):
            self.engine.command("select_peer", peer=peer_id)
            self.network.select(peer_id)
            self.notice(tr('已选择此电脑作为内容共享目标。屏幕布局决定键鼠切换方向。'))
            return
        self.notice(tr('正在连接；首次连接请在两台电脑上核对配对码。'))
        if not self.demo:
            self.network.connect(peer["host"], peer.get("port", 24872), peer_id)

    def connect_ip(self):
        host = self.manual_ip.text().strip()
        try:
            socket.inet_pton(socket.AF_INET, host)
        except OSError:
            self.notice(tr('请输入有效的 IPv4 地址，例如 192.168.1.20。'))
            return
        if not self.demo:
            self.network.connect(host)

    def forget_selected(self):
        peer_id = self.selected_id()
        if not peer_id:
            return
        self.settings.forget(peer_id)
        session = self.network.peer_session(peer_id)
        if session:
            session.stop()
        self.refresh_peers()
        self.notice(tr('配对已取消。下次连接将重新核对配对码。'))

    def pair_dialog(self, session):
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('核对两台电脑的配对码'))
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(30, 28, 30, 26)
        layout.setSpacing(6)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(52, 52))
        layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(6)
        title = text_label(tr('核对配对码'), "section")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        name = text_label(tr('请求连接：{p0}', p0=session.peer_name), "hint", True)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name)
        layout.addSpacing(16)
        code = QWidget()
        code.setAccessibleName(tr('配对码 {p0}', p0=session.code))
        digits = QHBoxLayout(code)
        digits.setContentsMargins(0, 0, 0, 0)
        digits.setSpacing(8)
        digits.addStretch()
        for index, digit in enumerate(session.code):
            if index == 3:
                digits.addSpacing(14)
            tile = text_label(digit, "digit")
            tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            digits.addWidget(tile)
        digits.addStretch()
        layout.addWidget(code)
        layout.addSpacing(16)
        label = text_label(tr('请确认两台电脑显示相同的六位数字，再在两端点击确认。\n配对成功后，对方可以在共享开启时控制本机键鼠。'),
                           "hint", True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        layout.addSpacing(18)
        row = QHBoxLayout()
        row.setSpacing(10)
        reject = QPushButton(tr('拒绝'))
        accept = QPushButton(tr('两端数字相同，确认配对'))
        accept.setObjectName("primary")
        row.addWidget(reject, 1)
        row.addWidget(accept, 2)
        layout.addLayout(row)
        accept.clicked.connect(dialog.accept)
        reject.clicked.connect(dialog.reject)
        key = id(session)
        self.dialogs[key] = dialog, session

        def finished(result):
            session.approve(result == QDialog.DialogCode.Accepted)
            self.dialogs.pop(key, None)
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.show_normal()
        dialog.open()

    def disconnected(self, session, message):
        self.engine.enqueue({"type": "disconnected", "session": session})
        self.bridge.error.emit(message)

    def save_settings(self):
        name = self.device_name.text().strip()
        if not name or any(ord(c) < 32 for c in name):
            self.notice(tr('请填写有效的本机名称。'))
            self.save_feedback(tr('请填写有效的本机名称。'), warning=True)
            return
        try:
            from .startup import set_enabled
            if self.startup.isChecked() != self.settings.get("startup"):
                set_enabled(self.startup.isChecked())
            self.settings.set(**self.form_values())
            self.engine.command("changed")
            self.device_info.setText(tr('设备指纹  {p0}', p0=self.identity.id[:16]))
            self.update_map()
            self.saved_form = self.form_values()
            self.form_saved_once = True
            self.settings_changed()
            self.notice(tr('设置已保存。请在两台电脑上分别设置对方所在方向。'))
        except OSError as exc:
            self.notice(exception_text(exc))
            self.save_feedback(exception_text(exc), warning=True)

    def check_permissions(self):
        if self.permission_guide is None:
            provider = (lambda: {"accessibility": False, "input_monitoring": False}) if self.demo else None
            self.permission_guide = PermissionGuide(
                self.backend, lambda: self.engine.command("permissions"), self,
                status_provider=provider, demo=self.demo)
        self.permission_guide.show()
        self.permission_guide.raise_()
        self.permission_guide.activateWindow()

    def closeEvent(self, event):
        if QSystemTrayIcon.isSystemTrayAvailable() and not self.closed:
            # Hiding the window must never leave the peer holding this
            # machine's mouse and keyboard behind it.
            if getattr(self, 'engine', None):
                self.engine.command("panic")
            self.hide()
            self.tray.showMessage(tr('MouseShare 正在后台运行'), tr('从托盘菜单退出；') + shortcut() + tr(' 随时收回键鼠。'))
            event.ignore()
        else:
            event.accept()

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        if self.file_clipboard:
            self.file_clipboard.stop()
        self.sharing.stop()
        if self.clipboard_sync:
            self.clipboard_sync.stop()
        self.edge_portal.close()
        if self.linux_session:
            self.linux_session.stop()
        if self.permission_guide:
            self.permission_guide.close()
        for dialog, session in list(self.dialogs.values()):
            session.approve(False)
            dialog.reject()
        if not self.demo:
            self.engine.stop()
            self.discovery.stop()
            self.network.stop()
        self.tray.hide()


def main():
    parser = argparse.ArgumentParser(description="MouseShare")
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Show UI without hooks or network")
    parser.add_argument("--smoke-test", action="store_true", help="Validate packaged dependencies then exit")
    parser.add_argument('--preview-drop', action='store_true', help='Preview the drop overlay for 12 seconds, without input hooks')
    parser.add_argument('--drop-smoke-test', action='store_true', help='Validate native blur and clean up without hooks or network')
    parser.add_argument('--paste-to', help='Receive offered files into this existing local folder')
    parser.add_argument('--diagnostics', action='store_true', help='Write local input health counts without key values')
    args = parser.parse_args()
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv[:1])
    app.setApplicationName("MouseShare")
    app.setOrganizationName("makeSail")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)
    if args.smoke_test:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            identity = Identity(Path(directory))
            assert identity.context(server=True)
            backend = create_backend(lambda event: True)
            assert backend.bounds()[2] > 0
            assert isinstance(backend.permissions(), bool)
        assert tr('共享设置') == '共享设置'
        print(f"MouseShare {__version__} smoke test OK ({platform.system()} {platform.machine()})")
        return 0
    if args.preview_drop or args.drop_smoke_test:
        from .drop_overlay import run_preview
        return run_preview(app, smoke=args.drop_smoke_test)
    # A fixed per-user local server prevents a second instance from installing duplicate hooks.
    name = f"MouseShare-{os.environ.get('USERNAME', os.environ.get('USER', 'desktop'))}"
    lock = lock_file(name)

    def outgoing():
        from .context_menu import request, diagnostics_request
        return diagnostics_request(True) if args.diagnostics else request(args.paste_to)

    if owner(lock):
        # Another copy is running, so wait while it finishes bringing up its socket.
        if forward(name, outgoing(), START_TIMEOUT):
            return 0
        discard(lock)
    elif forward(name, outgoing(), PROBE_TIMEOUT):
        return 0
    if not claim(lock):
        if forward(name, outgoing(), START_TIMEOUT):
            return 0
        # The lock may be stale: a killed copy never ran its atexit release.
        # A dead recorded pid (or our own) means nobody owns the service, so
        # drop the file and try once more instead of failing until a human
        # deletes the lock by hand.
        if owner(lock) is None:
            discard(lock)
            if claim(lock):
                atexit.register(release, lock)
            else:
                QMessageBox.critical(None, "MouseShare", tr('无法启动本机控制服务，请重启应用。'))
                return 1
        else:
            QMessageBox.critical(None, "MouseShare", tr('无法启动本机控制服务，请重启应用。'))
            return 1
    else:
        atexit.register(release, lock)
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):
        QMessageBox.critical(None, "MouseShare", tr('无法启动本机控制服务，请重启应用。'))
        return 1
    try:
        settings = Settings()
        set_dock_icon(settings.get('dock_icon', False))
        window = MainWindow(settings, Identity(settings.directory), args.demo)
        window.diagnostics_requested = args.diagnostics
    except Exception as exc:
        QMessageBox.critical(None, "MouseShare", tr('启动失败：{p0}。请检查本机配置文件。', p0=type(exc).__name__))
        return 1

    def activate():
        pending = server.nextPendingConnection()
        if not pending:
            return
        data = bytearray()
        def read():
            data.extend(bytes(pending.readAll()))
            if len(data) > 8192:
                pending.abort()
                return
            if b'\n' not in data and data != b'show':
                return
            try:
                from .context_menu import parse_request
                destination = parse_request(bytes(data))
                if isinstance(destination, dict):
                    window.diagnostics_requested = destination['diagnostics']
                    window.save_diagnostics()
                elif destination and window.file_clipboard:
                    window.file_clipboard.action('paste', destination)
                else:
                    window.show_normal()
            except (ValueError, OSError):
                window.notice(tr('无法接收文件，请确认目标文件夹仍然存在'))
            pending.disconnectFromServer()
        pending.readyRead.connect(read)
        pending.disconnected.connect(pending.deleteLater)
        QTimer.singleShot(3000, pending.abort)
        read()
    server.newConnection.connect(activate)
    app.aboutToQuit.connect(window.shutdown)
    if args.paste_to and window.file_clipboard:
        def initial_paste():
            try:
                from .context_menu import parse_request, request
                window.file_clipboard.action('paste', parse_request(request(args.paste_to)))
            except (ValueError, OSError):
                window.notice(tr('无法接收文件，请确认目标文件夹仍然存在'))
        QTimer.singleShot(0, initial_paste)
    if not args.minimized:
        window.show()
        if sys.platform == "darwin" and not args.demo and not window.backend.permissions():
            QTimer.singleShot(0, window.check_permissions)
    return app.exec()

