"""Draggable computer rectangles with explicit save, numeric editing and safe snapping."""
import copy

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsObject, QGraphicsScene, QHBoxLayout, QVBoxLayout

from .displays import contact_rects, rectangles
from .i18n import exception_text, tr
from .i18n_widgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsView,
    QPushButton,
    QWidget,
)
from .topology import (MAX_DEVICES, Rect, default_nodes, screens_overlap,
                      validate_layout)
from .ui import ACCENT, ACCENT_SOFT, INK, INK_2, LINE, card, text_label


class DeviceNode(QGraphicsObject):
    def __init__(self, editor, key):
        super().__init__()
        self.editor, self.key = editor, key
        self.setFlags(self.GraphicsItemFlag.ItemIsSelectable)
        # Dragging is handled per physical display, not for the whole computer:
        # a multi-monitor group must be splittable onto different neighbours.
        self.drag = None

    def bounds(self):
        """Item-local drawing bounds: every display this computer occupies."""
        node = self.editor.nodes[self.key]
        rects = self.editor.display_rects(self.key)
        left = min([0]+[m['x']-node['x'] for m in rects])
        top = min([0]+[m['y']-node['y'] for m in rects])
        right = max([node['w']]+[m['x']+m['w']-node['x'] for m in rects])
        bottom = max([node['h']]+[m['y']+m['h']-node['y'] for m in rects])
        return QRectF(left, top, right-left, bottom-top)

    def boundingRect(self):
        return self.bounds().adjusted(-4, -4, 4, 4)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        meta = self.editor.metadata.get(self.key, {})
        local = self.key == self.editor.local_id
        active = local or meta.get('connected')
        node = self.editor.nodes[self.key]
        rect = self.bounds()
        split = self.key in self.editor.monitors
        monitors = self.editor.display_rects(self.key)
        dragged = (self.drag or {}).get('index')
        if len(monitors) > 1 and not split:
            painter.setBrush(QColor('#e2e9ec'))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 12, 12)
        for index, monitor in enumerate(monitors):
            box = QRectF(monitor['x']-node['x'], monitor['y']-node['y'],
                         monitor['w'], monitor['h'])
            r = box.adjusted(2, 2, -2, -2)
            moving = index == dragged
            painter.setBrush(QColor(ACCENT_SOFT if self.isSelected() or moving else '#ffffff'))
            painter.setPen(QPen(QColor(ACCENT if self.isSelected() or moving else LINE),
                                3 if self.isSelected() or moving else 2))
            painter.drawRoundedRect(r, 10, 10)
        font = QFont(painter.font())
        font.setPixelSize(16 if len(monitors) > 1 else 20)
        font.setBold(True)
        painter.setPen(QColor(INK if active else INK_2))
        for index, monitor in enumerate(monitors):
            box = QRectF(monitor['x']-node['x'], monitor['y']-node['y'],
                         monitor['w'], monitor['h'])
            painter.setFont(font)
            name = painter.fontMetrics().elidedText(meta.get('name', self.key[:8]), Qt.TextElideMode.ElideRight,
                                                   max(30, int(box.width()-30)))
            painter.drawText(box.adjusted(15, 15, -15, -38), Qt.AlignmentFlag.AlignCenter, name)
            if len(monitors) > 1:
                painter.setFont(self.small(painter, 12))
                painter.setPen(QColor(INK_2))
                painter.drawText(box.adjusted(8, 8, -8, -8),
                                 Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                                 self.editor.monitor_label(self.key, index))
            footer_font = self.small(painter, 12)
            painter.setFont(footer_font)
            painter.setPen(QColor(INK_2))
            status = tr('本机') if local else (tr('已连接') if active else tr('离线／未配对'))
            if len(monitors) > 1:
                status = str(status)+f' · {index+1}/{len(monitors)}'
            painter.drawText(box.adjusted(10, 30, -10, -10),
                             Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, status)
        painter.setBrush(QColor(ACCENT if active else '#aab5b9'))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(rect.right()-16, rect.top()+16), 4, 4)

    @staticmethod
    def small(painter, size):
        font = QFont(painter.font())
        font.setPixelSize(size)
        font.setBold(False)
        return font

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag = self.editor.hit_monitor(self, event.pos(), event.scenePos())
            if self.drag:
                # Without this the scene never hands us the move events, since
                # the view no longer runs its own hand-drag.
                self.grabMouse()
                event.accept()
                super().mousePressEvent(event)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag:
            self.editor.drag_monitor(self, event.scenePos())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        drag, self.drag = self.drag, None
        if drag:
            self.ungrabMouse()
        super().mouseReleaseEvent(event)
        if drag:
            self.editor.finish_monitor_drag(self, drag)


def shared_edge(a, b, tolerance=1.0):
    """The segment where two rectangles touch, or None.

    Crossing works along shared edges only, so showing them tells the user
    which pairs of screens can actually hand over to each other.
    """
    if abs(a['x']+a['w']-b['x']) <= tolerance or abs(b['x']+b['w']-a['x']) <= tolerance:
        top, bottom = max(a['y'], b['y']), min(a['y']+a['h'], b['y']+b['h'])
        if bottom-top > 0.5:
            x = b['x'] if abs(a['x']+a['w']-b['x']) <= tolerance else a['x']
            return dict(vertical=True, x=x, start=top, end=bottom)
    if abs(a['y']+a['h']-b['y']) <= tolerance or abs(b['y']+b['h']-a['y']) <= tolerance:
        left, right = max(a['x'], b['x']), min(a['x']+a['w'], b['x']+b['w'])
        if right-left > 0.5:
            y = b['y'] if abs(a['y']+a['h']-b['y']) <= tolerance else a['y']
            return dict(vertical=False, y=y, start=left, end=right)
    return None


class ContactEdges(QGraphicsObject):
    """Accent stripes on every pair of touching screens across two computers."""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setZValue(-1)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rects = [(key, rect) for key in self.editor.nodes
                 for rect in self.editor.display_rects(key)]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#f0b429'))
        for index, (key, a) in enumerate(rects):
            for other, b in rects[index+1:]:
                if key == other:
                    continue  # Displays of one computer are crossed by the system.
                edge = shared_edge(a, b)
                if not edge:
                    continue
                painter.drawRect(QRectF(edge['x']-2, edge['start'], 4, edge['end']-edge['start'])
                                 if edge['vertical'] else
                                 QRectF(edge['start'], edge['y']-2, edge['end']-edge['start'], 4))

    def bounds(self):
        rects = [rect for key in self.editor.nodes for rect in self.editor.display_rects(key)]
        if not rects:
            return QRectF()
        left = min(r['x'] for r in rects)
        top = min(r['y'] for r in rects)
        right = max(r['x']+r['w'] for r in rects)
        bottom = max(r['y']+r['h'] for r in rects)
        return QRectF(left, top, right-left, bottom-top).adjusted(-8, -8, 8, 8)

    def boundingRect(self):
        # Computed from the rectangles themselves: asking the scene here would
        # recurse right back into this item.
        return self.bounds()


class DesktopView(QGraphicsView):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not getattr(self, 'fitted', False) and self.scene() and self.scene().items():
            self.fitInView(self.scene().itemsBoundingRect().adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)
            self.fitted = True

    def empty_space(self, event):
        return self.itemAt(event.position().toPoint()) is None

    def mousePressEvent(self, event):
        # ScrollHandDrag would swallow the press and pan instead of letting the
        # device screens be dragged, so panning is done by hand on empty space.
        if event.button() == Qt.MouseButton.LeftButton and self.empty_space(event):
            self.panning = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        start = getattr(self, 'panning', None)
        if start is not None:
            delta = self.mapToScene(event.position().toPoint())-self.mapToScene(start.toPoint())
            for bar, amount in ((self.horizontalScrollBar(), delta.x()),
                                (self.verticalScrollBar(), delta.y())):
                bar.setValue(int(bar.value()-amount))
            self.panning = event.position()
            event.accept()
            return
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.empty_space(event)
                       else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, 'panning', None) is not None:
            self.panning = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class LayoutEditor(QWidget):
    def __init__(self, settings, local_id, apply_layout, apply_system=None, parent=None):
        super().__init__(parent)
        self.settings, self.local_id, self.apply_layout = settings, local_id, apply_layout
        # Lets the arranged screens be pushed into the operating system, so the
        # movement between two screens of one computer matches the canvas.
        self.apply_system = apply_system
        self.nodes, self.items, self.metadata, self.excluded = {}, {}, {}, set()
        # Per-monitor placement for multi-monitor computers (system order).
        # Empty until a screen is dragged out of the system arrangement.
        self.monitors = {}
        self.dirty = False
        self.saving_revision = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.hint = text_label(tr('每块显示器都独立可拖动，边缘贴齐后即出现黄线。同一电脑的屏幕之间由系统搬运鼠标，摆好后点「应用到系统排列」，让 Windows 也按这个位置排列；保存后跨屏生效。'), 'hint', True)
        layout.addWidget(self.hint)
        self.scene = QGraphicsScene(self)
        self.scene.selectionChanged.connect(self.selection_changed)
        self.view = DesktopView(self.scene)
        self.view.setAccessibleName(tr('可拖动的电脑屏幕布局'))
        self.view.setFixedHeight(290)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.view.setCursor(Qt.CursorShape.OpenHandCursor)
        self.view.setStyleSheet('QGraphicsView {background:#eef3f4; border:1px solid #dce5e8; border-radius:12px;}')
        layout.addWidget(self.view)
        tools = QHBoxLayout()
        for label, callback in [(tr('适应画布'), self.fit), ('＋', lambda: self.view.scale(1.2, 1.2)),
                                ('－', lambda: self.view.scale(1/1.2, 1/1.2)), (tr('重置排列'), self.reset_nodes),
                                (tr('移出所选设备'), self.remove_selected),
                                (tr('应用到系统排列'), self.apply_system_arrangement)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            tools.addWidget(button)
        layout.addLayout(tools)
        box, body = card(tr('精确位置与显示大小'))
        form = QFormLayout()
        self.device = QComboBox()
        self.device.currentIndexChanged.connect(self.select_combo)
        form.addRow(tr('选择设备'), self.device)
        self.x, self.y, self.height = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        for label, spin in [(tr('横向位置'), self.x), (tr('纵向位置'), self.y), (tr('显示高度'), self.height)]:
            spin.setRange(-8192, 8192) if spin is not self.height else spin.setRange(64, 720)
            spin.setDecimals(1)
            spin.setSingleStep(10)
            spin.setAccessibleName(label)
            spin.valueChanged.connect(self.numeric_changed)
            form.addRow(label, spin)
        body.addLayout(form)
        body.addWidget(text_label(tr('显示大小用于匹配不同尺寸／缩放的电脑；不修改系统分辨率。未接触的屏幕不会跨屏。每两台需要直接交接或传文件的电脑都要先完成配对。'), 'hint', True))
        layout.addWidget(box)
        self.enabled = QCheckBox(tr('按此布局跨屏，并同步到已配对的新版本设备'))
        self.enabled.setChecked(True)
        self.enabled.toggled.connect(self.mark_dirty)
        layout.addWidget(self.enabled)
        self.auto_arrange = QCheckBox(tr('保存时自动把本机屏幕排列同步到系统（Windows）'))
        layout.addWidget(self.auto_arrange)
        row = QHBoxLayout()
        self.save = QPushButton(tr('保存并应用布局'))
        self.save.setObjectName('primary')
        self.save.clicked.connect(self.save_layout)
        row.addWidget(self.save)
        reload = QPushButton(tr('重新载入'))
        reload.clicked.connect(self.reload)
        row.addWidget(reload)
        self.feedback = text_label(tr('拖动后保存生效'), 'hint', True)
        row.addWidget(self.feedback, 1)
        layout.insertLayout(1, row)
        layout.removeWidget(self.enabled)
        layout.removeWidget(self.auto_arrange)
        layout.insertWidget(2, self.enabled)
        layout.insertWidget(3, self.auto_arrange)

    def update_devices(self, metadata):
        self.metadata = metadata
        if not self.nodes:
            stored = self.settings.get('desktop_layout')
            if stored and self.local_id in stored.get('nodes', {}):
                self.load(stored)
            else:
                self.nodes = default_nodes(self.local_id, [k for k in metadata if k != self.local_id][:7],
                                           self.settings.get('side'), self.screens())
                self.rebuild()
                self.mark_dirty()
        for key in metadata:
            if key not in self.nodes and key not in self.excluded and len(self.nodes) < MAX_DEVICES:
                right = max(n['x']+n['w'] for n in self.nodes.values())
                width, height = self.screens().get(key, (1920, 1080))
                self.nodes[key] = Rect(right, 0, max(100, min(720, 180*width/max(1, height))), 180).data()
                self.rebuild()
                self.mark_dirty()
        for item in self.items.values():
            item.update()
        selected = self.device.currentData()
        blocker = QSignalBlocker(self.device)
        self.device.clear()
        for key in self.nodes:
            self.device.addItem(metadata.get(key, {}).get('name', key[:8]), key)
        if selected in self.nodes:
            self.device.setCurrentIndex(self.device.findData(selected))
        del blocker
        self.select_combo()

    def screens(self):
        return {key: (meta.get('screen', {}).get('w', 1920), meta.get('screen', {}).get('h', 1080))
                for key, meta in self.metadata.items()}

    def load(self, data):
        self.nodes = copy.deepcopy(data['nodes'])
        self.excluded = set(data.get('excluded', []))
        # Placements for devices that are gone, or for a different number of
        # displays than the system reports now, are dropped here (and
        # contact_rects would ignore them at use time anyway).
        self.monitors = {}
        for key, placed in copy.deepcopy(data.get('monitors', {})).items():
            if key in self.nodes:
                screen = self.screen_dict(key)
                if len(placed) == len(screen.get('monitors') or [screen]):
                    self.monitors[key] = placed
        blocker = QSignalBlocker(self.enabled)
        self.enabled.setChecked(data.get('enabled', False))
        del blocker
        self.rebuild()
        self.dirty = False
        self.save.setEnabled(False)

    def rebuild(self):
        self.items.clear()
        self.scene.clear()
        self.contacts = ContactEdges(self)
        self.scene.addItem(self.contacts)
        for key, node in self.nodes.items():
            item = DeviceNode(self, key)
            item.setPos(node['x'], node['y'])
            self.scene.addItem(item)
            self.items[key] = item
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-160, -160, 160, 160))

    def fit(self):
        if self.items:
            self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)

    def mark_dirty(self, *args):
        self.dirty = True
        self.save.setEnabled(True)
        self.feedback.setText(tr('布局有未保存的更改'))

    def screen_dict(self, key):
        return self.metadata.get(key, {}).get('screen') or dict(w=1920, h=1080)

    def monitor_rects(self, key):
        """Absolute canvas rectangles of every physical display, system order."""
        return contact_rects(key, self.nodes[key], self.screen_dict(key), self.monitors)

    def display_rects(self, key):
        """What to draw: the committed placement, with any live drag applied."""
        rects = [dict(rect) for rect in self.monitor_rects(key)]
        drag = getattr(self.items.get(key), 'drag', None)
        if drag and drag.get('current') and 0 <= drag['index'] < len(rects):
            rects[drag['index']] = drag['current']
        return rects

    def monitor_label(self, key, index):
        """Which physical display this block is, so the mapping is not a guess."""
        screen = self.screen_dict(key)
        monitors = rectangles(screen)
        if index >= len(monitors):
            return ''
        monitor = monitors[index]
        return '{w}×{h} @({x},{y}){primary}'.format(
            w=monitor['w'], h=monitor['h'], x=monitor['x'], y=monitor['y'],
            primary=' ' + tr('主屏') if monitor.get('primary') else '')

    def occupied_rects(self, skip_key=None, skip_index=None):
        """Every rectangle on the canvas, for snapping and overlap checks."""
        result = []
        for key in self.nodes:
            for index, rect in enumerate(self.monitor_rects(key)):
                if key == skip_key and index == skip_index:
                    continue
                result.append(rect)
        return result

    def hit_monitor(self, item, local_pos, scene_pos):
        node = self.nodes[item.key]
        x, y = local_pos.x()+node['x'], local_pos.y()+node['y']
        for index, rect in enumerate(self.monitor_rects(item.key)):
            if rect['x'] <= x <= rect['x']+rect['w'] and rect['y'] <= y <= rect['y']+rect['h']:
                return dict(index=index, anchor=QPointF(scene_pos), start=dict(rect), moved=False)
        return None

    def snap_rect(self, target, skip_key, skip_index):
        """Pull an edge onto a neighbour within reach, so blocks click together."""
        for other in self.occupied_rects(skip_key, skip_index):
            for candidate in (other['x']-target['w'], other['x']+other['w']):
                if abs(target['x']-candidate) <= 12:
                    target['x'] = candidate
            for candidate in (other['y']-target['h'], other['y']+other['h']):
                if abs(target['y']-candidate) <= 12:
                    target['y'] = candidate
        return target

    def drag_monitor(self, item, scene_pos, drag=None):
        drag = drag or getattr(item, 'drag', None)
        if not drag:
            return
        dx, dy = scene_pos.x()-drag['anchor'].x(), scene_pos.y()-drag['anchor'].y()
        drag['moved'] = True
        drag['current'] = dict(x=round((drag['start']['x']+dx)/5)*5,
                               y=round((drag['start']['y']+dy)/5)*5,
                               w=drag['start']['w'], h=drag['start']['h'])
        # Snap while dragging too: the following feel is what tells the user the
        # two screens will actually trade the cursor.
        self.snap_rect(drag['current'], item.key, drag['index'])
        item.prepareGeometryChange()
        item.update()
        # Keep dragging possible past the edges of the current canvas.
        self.scene.setSceneRect(self.scene.sceneRect().united(
            QRectF(drag['current']['x'], drag['current']['y'],
                   drag['current']['w'], drag['current']['h']).adjusted(-160, -160, 160, 160)))
        if getattr(self, 'contacts', None):
            self.contacts.prepareGeometryChange()
            self.contacts.update()

    def finish_monitor_drag(self, item, drag):
        index = drag['index']
        if not drag.get('moved'):
            return
        target = self.snap_rect(drag['current'], item.key, index)
        rects = [dict(r) for r in self.monitor_rects(item.key)]
        rects[index] = target
        # A single display updates the node itself; a split-off display of a
        # multi-monitor computer lives in the per-monitor placement.
        if len(rects) == 1:
            self.nodes[item.key] = dict(target)
        else:
            self.monitors[item.key] = rects
        # Free placement: a screen stays exactly where it was dropped, however
        # it overlaps. Nothing here may move it back.
        self.validate_nodes()
        self.mark_dirty()
        item.prepareGeometryChange()
        item.update()
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-160, -160, 160, 160))
        self.selection_changed()
        self.warn_overlaps()

    def warn_overlaps(self):
        """Say which stacks cannot route, instead of quietly moving them back."""
        try:
            clashes = [pair for pair in screens_overlap(self.draft()) if not pair[4]]
        except ValueError:
            return
        if clashes:
            self.feedback.setText(tr('有 {p0} 处屏幕重叠，重叠的边缘不会跨屏', p0=len(clashes)))

    def draft(self):
        return dict(enabled=self.enabled.isChecked(), revision=1, author=self.local_id,
                    nodes=self.nodes, excluded=sorted(self.excluded),
                    monitors={k: v for k, v in self.monitors.items() if k in self.nodes})

    def validate_nodes(self):
        return validate_layout(self.draft())

    def system_positions(self):
        """Native pixel positions for this computer's own screens, or None.

        Canvas units are arbitrary (the user resizes blocks to compare sizes),
        so only the spacing between the blocks carries meaning — it is divided
        by the common scale to land back on real pixels.
        """
        if self.local_id not in self.nodes:
            return None
        screen = self.screen_dict(self.local_id)
        monitors = rectangles(screen)
        rects = self.monitor_rects(self.local_id)
        if len(rects) != len(monitors) or len(rects) < 2:
            return None
        native = sum(monitor['w'] for monitor in monitors)
        if not native:
            return None
        scale = sum(rect['w'] for rect in rects)/native
        left = min(rect['x'] for rect in rects)
        top = min(rect['y'] for rect in rects)
        return [((rect['x']-left)/scale, (rect['y']-top)/scale) for rect in rects]

    def system_arrange(self):
        """Tell the operating system to lay this computer out as drawn."""
        positions = self.system_positions()
        if positions is None:
            return False, tr('本机只有一块显示器，无需排列')
        if not self.apply_system:
            return False, tr('当前平台不支持自动排列，请在系统显示设置中手动调整')
        try:
            reply = self.apply_system(positions)
        except Exception as exc:
            return False, exception_text(exc)
        return (False, str(reply)) if reply else (True, tr('已按布局调整本机显示器排列'))

    def apply_system_arrangement(self):
        _, message = self.system_arrange()
        self.feedback.setText(message)

    def selection_changed(self):
        selected = self.scene.selectedItems()
        key = selected[0].key if selected else self.device.currentData()
        if key not in self.nodes:
            return
        blockers = [QSignalBlocker(control) for control in (self.device, self.x, self.y, self.height)]
        self.device.setCurrentIndex(self.device.findData(key))
        node = self.nodes[key]
        self.x.setValue(node['x'])
        self.y.setValue(node['y'])
        self.height.setValue(node['h'])
        del blockers

    def select_combo(self):
        key = self.device.currentData()
        if key in self.items:
            self.scene.clearSelection()
            self.items[key].setSelected(True)
            self.selection_changed()

    def numeric_changed(self, *args):
        key = self.device.currentData()
        if key not in self.nodes:
            return
        old = self.nodes[key]
        node = Rect(self.x.value(), self.y.value(), self.height.value()*old['w']/old['h'], self.height.value()).data()
        # The panel moves a whole computer; split-off displays travel with it.
        if key in self.monitors:
            kx, ky = node['w']/max(1, old['w']), node['h']/max(1, old['h'])
            self.monitors[key] = [dict(x=node['x']+(m['x']-old['x'])*kx, y=node['y']+(m['y']-old['y'])*ky,
                                       w=m['w']*kx, h=m['h']*ky) for m in self.monitors[key]]
        self.items[key].prepareGeometryChange()
        self.nodes[key] = node
        self.items[key].setPos(node['x'], node['y'])
        self.items[key].update()
        self.mark_dirty()

    def reset_nodes(self):
        self.excluded.clear()
        self.monitors = {}
        self.nodes = default_nodes(self.local_id, [k for k in self.metadata if k != self.local_id][:7], screens=self.screens())
        self.rebuild()
        self.mark_dirty()
        self.fit()

    def remove_selected(self):
        key = self.device.currentData()
        if key and key != self.local_id:
            self.nodes.pop(key, None)
            self.monitors.pop(key, None)
            self.excluded.add(key)
            self.rebuild()
            self.update_devices(self.metadata)
            self.mark_dirty()

    def save_layout(self):
        old = self.settings.get('desktop_layout', {})
        monitors = {k: v for k, v in self.monitors.items() if k in self.nodes}
        data = dict(enabled=self.enabled.isChecked(), revision=old.get('revision', 0)+1,
                    author=self.local_id, nodes=self.nodes, excluded=sorted(self.excluded),
                    monitors=monitors)
        try:
            data = validate_layout(data)
        except ValueError as exc:
            self.feedback.setText(exception_text(exc))
            return
        self.saving_revision = data['revision']
        self.apply_layout(data)
        self.feedback.setText(tr('正在应用布局…'))
        if self.auto_arrange.isChecked():
            arranged, message = self.system_arrange()
            self.feedback.setText(tr('正在应用布局，并同步本机屏幕排列…') if arranged else message)

    def applied(self, data):
        if self.dirty and not (data['author'] == self.local_id and data['revision'] == self.saving_revision):
            self.feedback.setText(tr('另一台电脑已更新布局；当前草稿保留。可重新载入，或保存覆盖。'))
            return
        self.load(data)
        self.update_devices(self.metadata)
        self.feedback.setText(tr('布局已应用，边缘接触处可跨屏'))

    def reload(self):
        data = self.settings.get('desktop_layout')
        if data:
            self.load(data)
            self.update_devices(self.metadata)
            self.fit()
