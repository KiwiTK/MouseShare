"""App messages keep their parameters, survive queued delivery and never translate user content."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mouseshare.app import Bridge, MainWindow
from mouseshare.config import Settings
from mouseshare.i18n import Message, exception_text, status_message, tr
from mouseshare.security import Identity
from mouseshare.ui import apply_theme


def test_parameters_survive_nesting_and_concatenation():
    name = '文件：复制文件 {keep me} 中文.exe'
    message = tr('本机 {p0}；对方 {p1} 位于{p2}', p0=name, p1='My PC', p2=tr('左侧'))
    assert message.render() == f'本机 {name}；对方 My PC 位于左侧'
    assert (message + ' / ' + name).render().endswith(name)
    assert name in message.render()


def test_exception_text_keeps_the_message_but_not_foreign_text():
    message = exception_text(OSError(tr('缺少 X11 系统库：{p0}。请按 Linux 安装说明安装依赖。', p0='libXi.so')))
    assert isinstance(message, Message)
    assert message.render().startswith('缺少 X11 系统库：libXi.so。')
    assert exception_text(OSError('OS message: 中文.txt')) == 'OS message: 中文.txt'


def test_status_message_recovers_app_authored_history_records():
    assert status_message('接收完成，SHA-256 校验通过') == '接收完成，SHA-256 校验通过'
    assert status_message(tr('等待对方接收')).render() == '等待对方接收'


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


@pytest.fixture
def window(app, tmp_path):
    settings = Settings(tmp_path)
    settings.set(name='中文设备 Preferences.txt')
    window = MainWindow(settings, Identity(tmp_path), demo=True)
    yield window
    window.shutdown()
    window.close()
    app.processEvents()


def test_queued_signals_deliver_messages_to_the_window(window, app):
    bridge, received = Bridge(), []
    bridge.error.connect(window.notice, Qt.ConnectionType.QueuedConnection)
    bridge.status.connect(lambda state: received.append(state['error']), Qt.ConnectionType.QueuedConnection)
    bridge.sharing.connect(window.sharing_event, Qt.ConnectionType.QueuedConnection)
    message = tr('连接已断开（{p0}）', p0='TimeoutError')
    bridge.error.emit(message)
    bridge.status.emit(dict(error=message))
    bridge.sharing.emit(dict(type='transfer', job='worker', direction='send', title='中文.txt',
                            detail=tr('等待对方接收'), total=10, done=0, paths=[], status='waiting'))
    app.processEvents()
    assert isinstance(received[0], Message)
    assert window.notice_label.text() == '连接已断开（TimeoutError）'
    assert window.share_panel.rows['worker'][2].text().startswith('等待对方接收')
    window.sharing.jobs.clear()


def test_ctl_names_stay_literal_while_interface_text_is_chinese(window):
    assert window.device_name.text() == '中文设备 Preferences.txt'
    assert window.nav_group.button(1).text() == '共享设置'
