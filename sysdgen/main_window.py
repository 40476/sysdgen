import os
import sys
import subprocess
import json
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
from datetime import datetime

from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QSize, QPointF, QRectF,
    QMargins, QRect, QLineF, QPoint
)
from PyQt6.QtGui import (
    QFont, QColor, QPalette, QIcon, QPainter, QPen, QBrush,
    QSyntaxHighlighter, QTextCharFormat,
    QFontDatabase, QPixmap, QPainterPath, QFontMetricsF,
    QAction
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel,
    QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QGroupBox, QFormLayout, QGridLayout,
    QScrollArea, QFrame, QToolBox, QToolButton, QMenu,
    QMenuBar, QStatusBar, QMessageBox, QFileDialog,
    QListWidget, QListWidgetItem, QHeaderView, QProgressBar,
    QDialog, QDialogButtonBox, QInputDialog, QPlainTextEdit,
    QStyleFactory, QStackedWidget,
    QGraphicsView, QGraphicsScene, QGraphicsEllipseItem,
    QGraphicsTextItem, QGraphicsLineItem, QGraphicsItem,
    QSizePolicy, QSpacerItem
)

from .models import (
    UnitFile, ALL_SECTIONS, SECTION_NAMES, SystemdOption,
    get_unit_type_enum, DEFAULT_SECTIONS_FOR_TYPE, SECTION_DESCRIPTIONS
)
from .systemd_manager import SystemdManager

# ─── KDE Theme Integration ──────────────────────────────────────────

def apply_kde_theme(app: QApplication):
    """Apply KDE theming to the application."""
    available = QStyleFactory.keys()
    for style_name in ["breeze", "Breeze", "oxygen", "Oxygen", "gtk2", "Fusion"]:
        if style_name in available:
            app.setStyle(QStyleFactory.create(style_name))
            break
    else:
        if "Fusion" in available:
            app.setStyle(QStyleFactory.create("Fusion"))

    app.setPalette(app.style().standardPalette() if app.style() else QPalette())

    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPointSize(10)
    app.setFont(font)

    app.setApplicationName("SysdGen")
    app.setApplicationDisplayName("Systemd Unit Generator")
    app.setOrganizationName("SysdGen")
    app.setOrganizationDomain("sysdgen.local")


# ─── Syntax Highlighter ────────────────────────────────────────────

class SystemdHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for systemd unit files."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules = []
        self._setup_rules()

    def _setup_rules(self):
        fmt_section = QTextCharFormat()
        fmt_section.setForeground(QColor("#008080"))
        fmt_section.setFontWeight(QFont.Weight.Bold)

        fmt_key = QTextCharFormat()
        fmt_key.setForeground(QColor("#007020"))

        fmt_comment = QTextCharFormat()
        fmt_comment.setForeground(QColor("#808080"))
        fmt_comment.setFontItalic(True)

        self._rules = [
            (r"^\[.*\]$", fmt_section),
            (r"^(#|;).*$", fmt_comment),
            (r"^[A-Za-z][A-Za-z0-9]*(?=\s*=)", fmt_key),
        ]

    def highlightBlock(self, text: str | None):
        if text is None:
            return
        for pattern, fmt in self._rules:
            for m in re.finditer(pattern, text, re.MULTILINE):
                self.setFormat(m.start(), m.end() - m.start(), fmt)

        eq_idx = text.find("=")
        if eq_idx >= 0:
            fmt_value = QTextCharFormat()
            fmt_value.setForeground(QColor("#7F0055"))
            self.setFormat(eq_idx + 1, len(text) - eq_idx - 1, fmt_value)


# ─── Journal Log Thread ────────────────────────────────────────────

class JournalLogThread(QThread):
    """Background thread for fetching journal logs."""
    log_ready = pyqtSignal(list)
    error_signal = pyqtSignal(str)

    def __init__(self, manager: SystemdManager, unit_name: str, lines: int = 100, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.unit_name = unit_name
        self.lines = lines
        self._running = True

    def run(self):
        try:
            log = self.manager.get_journal_log(self.unit_name, self.lines)
            if self._running:
                self.log_ready.emit(log)
        except Exception as e:
            self.error_signal.emit(str(e))

    def stop(self):
        self._running = False


class JournalLiveWatcher(QThread):
    """Thread that periodically polls journalctl for new entries."""
    new_logs = pyqtSignal(list)

    def __init__(self, manager: SystemdManager, unit_name: str, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.unit_name = unit_name
        self._running = True
        self._last_lines: Set[str] = set()

    def run(self):
        import time
        while self._running:
            try:
                logs = self.manager.get_journal_log(self.unit_name, 50)
                new = []
                for l in logs:
                    l_s = l.strip()
                    if l_s and l_s not in self._last_lines:
                        new.append(l_s)
                if new:
                    self._last_lines = set(log.strip() for log in logs)
                    self.new_logs.emit(new)
                time.sleep(3)
            except Exception:
                time.sleep(5)

    def set_unit(self, unit_name: str):
        self.unit_name = unit_name
        self._last_lines.clear()

    def stop(self):
        self._running = False


# ─── Option Widget Factory ─────────────────────────────────────────

def create_option_widget(opt: SystemdOption, parent=None) -> QWidget:
    """Create the appropriate widget for a systemd option."""
    container = QWidget(parent)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)

    tooltip = opt.description or opt.label
    if opt.enum:
        tooltip += "\n\nPossible values: " + ", ".join(opt.enum)
    if opt.deprecated:
        tooltip += "\n[DEPRECATED]"
    container.setToolTip(tooltip)

    if opt.option_type == "boolean":
        cb = QCheckBox()
        cb.setText("")
        cb.setToolTip(tooltip)
        layout.addWidget(cb)
        container._widget = cb
    elif opt.enum:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems([""] + opt.enum)
        combo.setToolTip(tooltip)
        combo.setMinimumWidth(200)
        layout.addWidget(combo)
        container._widget = combo
    elif opt.option_type in ("integer", "unsigned"):
        spin = QSpinBox()
        if opt.option_type == "unsigned":
            spin.setMinimum(0)
        else:
            spin.setMinimum(-1000000)
        spin.setMaximum(1000000000)
        if isinstance(opt.default, int):
            spin.setValue(opt.default)
        spin.setToolTip(tooltip)
        layout.addWidget(spin)
        container._widget = spin
    elif opt.option_type == "duration":
        edit = QLineEdit()
        edit.setPlaceholderText("e.g. 30s, 5m, 1h, 1d")
        if isinstance(opt.default, int) and opt.default:
            from .models import _format_duration
            edit.setText(_format_duration(opt.default))
        edit.setToolTip(tooltip)
        layout.addWidget(edit)
        container._widget = edit
    elif opt.option_type == "size":
        edit = QLineEdit()
        edit.setPlaceholderText("e.g. 512M, 1G")
        edit.setToolTip(tooltip)
        layout.addWidget(edit)
        container._widget = edit
    elif opt.option_type == "percent":
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setSuffix("%")
        spin.setToolTip(tooltip)
        layout.addWidget(spin)
        container._widget = spin
    elif opt.multiline:
        text = QTextEdit()
        text.setToolTip(tooltip)
        text.setMaximumHeight(80)
        text.setPlaceholderText("One " + opt.label + " per line...")
        layout.addWidget(text)
        container._widget = text
    else:
        edit = QLineEdit()
        edit.setPlaceholderText(opt.placeholder or ("Enter " + opt.label + "..."))
        edit.setToolTip(tooltip)
        layout.addWidget(edit)
        container._widget = edit

    return container


def get_option_value(widget: QWidget, opt: SystemdOption) -> Any:
    """Get the value from an option widget."""
    w = widget._widget
    if opt.option_type == "boolean":
        return w.isChecked()
    if opt.option_type in ("integer",):
        try:
            return int(w.value())
        except (ValueError, OverflowError):
            return 0
    if opt.option_type in ("unsigned",):
        try:
            return max(0, w.value())
        except (ValueError, OverflowError):
            return 0
    if opt.option_type == "percent":
        return w.value()
    if opt.option_type == "duration":
        from .models import _parse_duration
        return _parse_duration(w.text())
    if opt.option_type == "size":
        from .models import _parse_size
        return _parse_size(w.text())
    if opt.multiline:
        text = w.toPlainText().strip()
        if not text:
            return []
        return [line.strip() for line in text.split("\n") if line.strip()]
    val = w.currentText() if isinstance(w, QComboBox) else w.text()
    return val.strip()


def set_option_value(widget: QWidget, opt: SystemdOption, value: Any):
    """Set the value on an option widget."""
    w = widget._widget
    if value is None:
        return
    if opt.option_type == "boolean":
        w.setChecked(bool(value))
    elif opt.option_type in ("integer", "unsigned", "percent"):
        try:
            w.setValue(int(value))
        except (ValueError, TypeError):
            pass
    elif opt.option_type == "duration":
        if isinstance(value, int):
            from .models import _format_duration
            w.setText(_format_duration(value))
        else:
            w.setText(str(value))
    elif opt.option_type == "size":
        if isinstance(value, (list, tuple)):
            w.setText(f"{value[0]}{value[1]}")
        else:
            w.setText(str(value))
    elif opt.multiline:
        if isinstance(value, list):
            w.setPlainText("\n".join(str(v) for v in value))
        else:
            w.setPlainText(str(value))
    else:
        if isinstance(w, QComboBox):
            idx = w.findText(str(value))
            if idx >= 0:
                w.setCurrentIndex(idx)
            else:
                w.setEditText(str(value))
        else:
            w.setText(str(value))


# ─── Editor Widget ─────────────────────────────────────────────────

class SectionEditorWidget(QWidget):
    """Widget for editing all options in a single section."""

    def __init__(self, section_name: str, parent=None):
        super().__init__(parent)
        self.section_name = section_name
        self.section_def = ALL_SECTIONS.get(section_name)
        self._option_widgets: Dict[str, QWidget] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        form = QFormLayout(container)
        form.setSpacing(4)
        form.setContentsMargins(10, 10, 10, 10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if not self.section_def:
            label = QLabel("No options defined for [" + self.section_name + "]")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #888; font-style: italic; padding: 20px;")
            form.addRow(label)
        else:
            basic_opts = []
            advanced_opts = []
            for key, opt in self.section_def.options.items():
                (advanced_opts if opt.advanced else basic_opts).append(opt)

            for opt in basic_opts:
                widget = create_option_widget(opt)
                self._option_widgets[opt.key] = widget
                label = QLabel(opt.label + ":")
                label.setToolTip(opt.description or opt.label)
                form.addRow(label, widget)

            if advanced_opts:
                sep = QLabel("<b>── Advanced Options ──</b>")
                sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
                sep.setStyleSheet("color: #888; padding: 8px;")
                form.addRow(sep)
                for opt in advanced_opts:
                    widget = create_option_widget(opt)
                    self._option_widgets[opt.key] = widget
                    label = QLabel(opt.label + ":")
                    label.setToolTip(opt.description or opt.label)
                    form.addRow(label, widget)

        scroll.setWidget(container)
        layout.addWidget(scroll)

    def load_from_unit(self, unit: UnitFile):
        """Load values from a UnitFile into the widgets."""
        section_values = unit.values.get(self.section_name, {})
        for key, widget in self._option_widgets.items():
            if self.section_def:
                opt = self.section_def.options.get(key)
                if opt:
                    val = section_values.get(key, opt.default)
                    set_option_value(widget, opt, val)

    def save_to_unit(self, unit: UnitFile):
        if self.section_name not in unit.values:
            unit.values[self.section_name] = {}
        if not self.section_def:
            return
        for key, widget in self._option_widgets.items():
            opt = self.section_def.options.get(key)
            if opt:
                val = get_option_value(widget, opt)
                default = opt.default
                if val == default or val == "" or val == [] or val is None or val == 0:
                    if key in unit.values[self.section_name]:
                        del unit.values[self.section_name][key]
                else:
                    unit.values[self.section_name][key] = val


# ─── Main Editor ───────────────────────────────────────────────────

class UnitEditorWidget(QWidget):
    """Complete unit file editor with all sections."""

    content_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._unit: Optional[UnitFile] = None
        self._editor_widgets: Dict[str, SectionEditorWidget] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()

        self._unit_type_combo = QComboBox()
        self._unit_type_combo.addItems(get_unit_type_enum())
        self._unit_type_combo.setToolTip("Unit type")
        self._unit_type_combo.currentTextChanged.connect(self._on_type_changed)
        toolbar.addWidget(QLabel("Type:"))
        toolbar.addWidget(self._unit_type_combo)

        self._filename_edit = QLineEdit()
        self._filename_edit.setPlaceholderText("unit-name.type (e.g. myapp.service)")
        self._filename_edit.setToolTip("Unit filename")
        toolbar.addWidget(QLabel("File:"))
        toolbar.addWidget(self._filename_edit, 1)

        self._save_btn = QPushButton("Save")
        self._save_btn.setToolTip("Save unit file")
        self._save_btn.clicked.connect(self._on_save)
        toolbar.addWidget(self._save_btn)

        self._file_path_label = QLabel("")
        self._file_path_label.setStyleSheet("color: #888; font-size: 9px;")
        toolbar.addWidget(self._file_path_label)

        layout.addLayout(toolbar)

        self._tab_widget = QTabWidget()
        self._tab_widget.setDocumentMode(True)
        layout.addWidget(self._tab_widget, 1)

    def _on_type_changed(self, unit_type: str):
        if self._unit is not None:
            name = self._filename_edit.text()
            if name:
                base = name.rsplit(".", 1)[0] if "." in name else name
                self._filename_edit.setText(base + "." + unit_type)
            self._unit.unit_type = unit_type
            self._rebuild_sections()
            self.content_changed.emit()

    def _rebuild_sections(self):
        self._tab_widget.clear()
        self._editor_widgets.clear()

        if self._unit is None:
            return

        sections = DEFAULT_SECTIONS_FOR_TYPE.get(self._unit.unit_type, ["Unit", "Install"])
        for sec_name in sections:
            editor = SectionEditorWidget(sec_name)
            self._editor_widgets[sec_name] = editor
            self._tab_widget.addTab(editor, sec_name)

        self.load_unit_into_editor()

    def set_unit(self, unit):
        self._unit = unit
        if unit:
            self._filename_edit.setText(unit.filename)
            self._unit_type_combo.blockSignals(True)
            idx = self._unit_type_combo.findText(unit.unit_type)
            if idx >= 0:
                self._unit_type_combo.setCurrentIndex(idx)
            self._unit_type_combo.blockSignals(False)
            self._file_path_label.setText("  " + (unit.filepath or ""))
        self._rebuild_sections()

    def get_unit(self):
        return self._unit

    def load_unit_into_editor(self):
        if self._unit is None:
            return
        for sec_name, editor in self._editor_widgets.items():
            editor.load_from_unit(self._unit)

    def save_editor_to_unit(self):
        if self._unit is None:
            return
        for sec_name, editor in self._editor_widgets.items():
            editor.save_to_unit(self._unit)
        self._unit.filename = self._filename_edit.text()

    def _on_save(self):
        if self._unit is None:
            QMessageBox.warning(self, "No Unit", "No unit file to save.")
            return
        self.save_editor_to_unit()

        path = self._unit.filepath or QFileDialog.getSaveFileName(
            self, "Save Unit File",
            str(Path.home()),
            "Systemd units (*.service *.socket *.timer *.path *.mount *.automount *.swap *.target *.device *.scope *.slice);;All Files (*)"
        )[0]

        if not path:
            return

        from .systemd_manager import SystemdManager
        mgr = SystemdManager()
        if mgr.save_unit_file(self._unit, path):
            self._unit.filepath = path
            self._file_path_label.setText("  " + path)
            QMessageBox.information(self, "Saved", "Saved to:\n" + path)
        else:
            QMessageBox.critical(self, "Error", "Failed to save:\n" + path)

    def get_unit_file_content(self) -> str:
        if self._unit is None:
            return ""
        self.save_editor_to_unit()
        return self._unit.to_unit_file_content()


# ─── Status Panel ──────────────────────────────────────────────────

class StatusPanel(QWidget):
    """Panel showing unit status, with start/stop/restart buttons."""
    unit_action = pyqtSignal(str, str)  # action, unit_name

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_unit: str = ""
        self._manager: Optional[SystemdManager] = None
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.setInterval(5000)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        btn_layout = QHBoxLayout()

        self._start_btn = QPushButton("Start")
        self._start_btn.setToolTip("Start the unit")
        self._start_btn.clicked.connect(lambda: self._do_action("start"))
        btn_layout.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setToolTip("Stop the unit")
        self._stop_btn.clicked.connect(lambda: self._do_action("stop"))
        btn_layout.addWidget(self._stop_btn)

        self._restart_btn = QPushButton("Restart")
        self._restart_btn.setToolTip("Restart the unit")
        self._restart_btn.clicked.connect(lambda: self._do_action("restart"))
        btn_layout.addWidget(self._restart_btn)

        self._enable_btn = QPushButton("Enable")
        self._enable_btn.setToolTip("Enable unit to start at boot")
        self._enable_btn.clicked.connect(lambda: self._do_action("enable"))
        btn_layout.addWidget(self._enable_btn)

        self._disable_btn = QPushButton("Disable")
        self._disable_btn.setToolTip("Disable unit from starting at boot")
        self._disable_btn.clicked.connect(lambda: self._do_action("disable"))
        btn_layout.addWidget(self._disable_btn)

        self._reload_btn = QPushButton("Daemon-Reload")
        self._reload_btn.setToolTip("Reload systemd daemon")
        self._reload_btn.clicked.connect(lambda: self._do_action("daemon-reload"))
        btn_layout.addWidget(self._reload_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._status_text = QTextEdit()
        self._status_text.setReadOnly(True)
        self._status_text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._status_text.setMaximumHeight(150)
        self._status_text.setStyleSheet("background: palette(base);")
        layout.addWidget(QLabel("<b>Unit Status:</b>"))
        layout.addWidget(self._status_text)

        info_layout = QHBoxLayout()
        self._active_label = QLabel("Active: --")
        self._active_label.setStyleSheet("font-weight: bold; padding: 2px 8px; border-radius: 3px;")
        info_layout.addWidget(self._active_label)
        self._enabled_label = QLabel("Enabled: --")
        self._enabled_label.setStyleSheet("font-weight: bold; padding: 2px 8px; border-radius: 3px;")
        info_layout.addWidget(self._enabled_label)
        info_layout.addStretch()
        layout.addLayout(info_layout)

    def set_manager(self, manager: SystemdManager):
        self._manager = manager

    def set_unit(self, unit_name: str):
        self._current_unit = unit_name
        self._update_buttons(False)
        self._refresh_status()
        self._timer.start()

    def clear_unit(self):
        self._current_unit = ""
        self._status_text.clear()
        self._active_label.setText("Active: --")
        self._enabled_label.setText("Enabled: --")
        self._update_buttons(False)
        self._timer.stop()

    def _update_buttons(self, enabled: bool):
        for btn in [self._start_btn, self._stop_btn, self._restart_btn,
                    self._enable_btn, self._disable_btn, self._reload_btn]:
            btn.setEnabled(enabled and bool(self._current_unit))

    def _do_action(self, action: str):
        if not self._current_unit or not self._manager:
            return
        self.unit_action.emit(action, self._current_unit)

        success = False
        output = ""
        if action == "start":
            success, output = self._manager.start_unit(self._current_unit)
        elif action == "stop":
            success, output = self._manager.stop_unit(self._current_unit)
        elif action == "restart":
            success, output = self._manager.restart_unit(self._current_unit)
        elif action == "enable":
            success, output = self._manager.enable_unit(self._current_unit)
        elif action == "disable":
            success, output = self._manager.disable_unit(self._current_unit)
        elif action == "daemon-reload":
            success, output = self._manager.daemon_reload()

        if not success and output:
            QMessageBox.warning(self, action.title() + " Failed", output)

        self._refresh_status()

    def _refresh_status(self):
        if not self._current_unit or not self._manager:
            return

        try:
            active = self._manager.is_active(self._current_unit)
            enabled = self._manager.is_enabled(self._current_unit)
        except Exception:
            active = False
            enabled = False

        if active:
            self._active_label.setText("Active")
            self._active_label.setStyleSheet(
                "font-weight: bold; color: #fff; background: #27ae60; padding: 2px 8px; border-radius: 3px;"
            )
        else:
            self._active_label.setText("Inactive")
            self._active_label.setStyleSheet(
                "font-weight: bold; color: #fff; background: #888; padding: 2px 8px; border-radius: 3px;"
            )

        if enabled:
            self._enabled_label.setText("Enabled")
            self._enabled_label.setStyleSheet(
                "font-weight: bold; color: #fff; background: #2980b9; padding: 2px 8px; border-radius: 3px;"
            )
        else:
            self._enabled_label.setText("Disabled")
            self._enabled_label.setStyleSheet(
                "font-weight: bold; color: #fff; background: #888; padding: 2px 8px; border-radius: 3px;"
            )

        status = self._manager.get_unit_status(self._current_unit)
        lines = status.split("\n")[:20]
        self._status_text.setPlainText("\n".join(lines))

        self._update_buttons(True)


# ─── Journal Log View ──────────────────────────────────────────────

class JournalLogView(QWidget):
    """Live journalctl log viewer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager: Optional[SystemdManager] = None
        self._current_unit: str = ""
        self._live_thread: Optional[JournalLiveWatcher] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        self._lines_spin = QSpinBox()
        self._lines_spin.setRange(10, 10000)
        self._lines_spin.setValue(100)
        self._lines_spin.setSingleStep(10)
        toolbar.addWidget(QLabel("Lines:"))
        toolbar.addWidget(self._lines_spin)

        self._priority_combo = QComboBox()
        self._priority_combo.addItems(["all", "emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"])
        toolbar.addWidget(QLabel("Priority:"))
        toolbar.addWidget(self._priority_combo)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._refresh_logs)
        toolbar.addWidget(self._refresh_btn)

        self._live_btn = QPushButton("Live")
        self._live_btn.setCheckable(True)
        self._live_btn.setChecked(False)
        self._live_btn.clicked.connect(self._toggle_live)
        toolbar.addWidget(self._live_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._clear_logs)
        toolbar.addWidget(self._clear_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._log_text.setMaximumBlockCount(10000)
        self._log_text.setStyleSheet("background: palette(base);")
        layout.addWidget(self._log_text, 1)

    def set_manager(self, manager: SystemdManager):
        self._manager = manager

    def set_unit(self, unit_name: str):
        self._current_unit = unit_name
        self._clear_logs()
        self._refresh_logs()
        if self._live_thread:
            self._live_thread.set_unit(unit_name)

    def clear_unit(self):
        self._current_unit = ""
        self._log_text.clear()
        if self._live_thread:
            self._live_thread.stop()
            self._live_thread = None
            self._live_btn.setChecked(False)

    def _refresh_logs(self):
        if not self._current_unit or not self._manager:
            return
        lines = self._lines_spin.value()
        priority = self._priority_combo.currentText()
        priority = None if priority == "all" else priority

        logs = self._manager.get_journal_log(self._current_unit, lines, priority=priority)
        self._log_text.setPlainText("\n".join(logs))

        sb = self._log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _toggle_live(self, enabled: bool):
        if not self._current_unit or not self._manager:
            self._live_btn.setChecked(False)
            return

        if enabled:
            self._live_thread = JournalLiveWatcher(self._manager, self._current_unit)
            self._live_thread.new_logs.connect(self._on_new_logs)
            self._live_thread.start()
            self._live_btn.setText("Stop Live")
        else:
            if self._live_thread:
                self._live_thread.stop()
                self._live_thread = None
            self._live_btn.setText("Live")

    def _on_new_logs(self, logs: List[str]):
        for l in logs:
            self._log_text.appendPlainText(l)
            block_count = self._log_text.blockCount()
            if block_count > 9000:
                cursor = self._log_text.textCursor()
                cursor.movePosition(cursor.MoveOperation.Start)
                cursor.movePosition(cursor.MoveOperation.Down, cursor.MoveMode.KeepAnchor, 100)
                cursor.removeSelectedText()

    def _clear_logs(self):
        self._log_text.clear()

    def closeEvent(self, event):
        if self._live_thread:
            self._live_thread.stop()
        super().closeEvent(event)


# ─── Dependency Graph View ─────────────────────────────────────────

class GraphBuildThread(QThread):
    """Async thread that collects graph DATA only.
    QGraphicsScene items MUST be created in the main thread."""

    data_ready = pyqtSignal(object, str)  # data dict, highlight_name

    def __init__(self, manager: SystemdManager, current_unit: str):
        super().__init__()
        self._manager = manager
        self._current_unit = current_unit

    def run(self):
        try:
            data = self._collect_data()
            if data is not None:
                self.data_ready.emit(data, self._current_unit)
        except Exception:
            pass

    def _collect_data(self):
        """Collect plain Python data - NO Qt objects created here."""
        result = {
            "nodes": [],      # list of {name, x, y, color_hex}
            "edges": [],      # list of {x1, y1, x2, y2}
            "error": None,
            "rect": None,     # (x, y, w, h) of bounding rect
        }

        if not self._manager:
            result["error"] = "No systemd manager available"
            return result

        try:
            all_units = self._manager.list_units()
        except Exception as e:
            result["error"] = "Failed to list units: " + str(e)
            return result

        unit_names = [u.get("name", "") for u in all_units
                     if u.get("name", "").endswith((".service", ".timer", ".socket", ".target", ".path"))]

        if not unit_names:
            result["error"] = "No units available"
            return result

        types_order = [".target", ".service", ".timer", ".socket", ".path"]
        units_by_type = {}
        for name in unit_names:
            ext = "." + name.rsplit(".", 1)[1] if "." in name else ""
            units_by_type.setdefault(ext, []).append(name)

        x_spacing = 100
        y_spacing = 80
        positions = {}
        row = 0
        for ext in types_order:
            if ext in units_by_type:
                names = units_by_type[ext]
                row_x = - (len(names) - 1) * x_spacing / 2
                for name in names:
                    positions[name] = (row_x, row * y_spacing - 200)
                    row_x += x_spacing
                row += 1

        for ext, names in units_by_type.items():
            if ext not in types_order:
                row_x = - (len(names) - 1) * x_spacing / 2
                for name in names:
                    positions[name] = (row_x, row * y_spacing - 200)
                    row_x += x_spacing
                row += 1

        # Build node data
        current_name = self._current_unit
        for name, (px, py) in positions.items():
            color = "#3498db"
            if name == current_name:
                color = "#e74c3c"
            elif ".timer" in name:
                color = "#9b59b6"
            elif ".target" in name:
                color = "#2ecc71"
            elif ".socket" in name:
                color = "#e67e22"
            elif ".path" in name:
                color = "#1abc9c"

            short_name = name.rsplit(".", 1)[0] if "." in name else name
            if len(short_name) > 12:
                short_name = short_name[:10] + ".."

            result["nodes"].append({
                "name": name,
                "short_name": short_name,
                "x": px,
                "y": py,
                "color": color,
                "is_current": (name == current_name),
            })

        # Build edge data
        for name, (px, py) in positions.items():
            try:
                proc = subprocess.run(
                    ["systemctl", "show", "--property=Wants,Requires,BindsTo,PartOf", name],
                    capture_output=True, text=True, timeout=2
                )
                for line in proc.stdout.strip().split("\n"):
                    if "=" in line:
                        _, val = line.split("=", 1)
                        for dep_name in val.split():
                            if dep_name in positions and dep_name != name:
                                dx, dy = positions[dep_name]
                                result["edges"].append({
                                    "x1": px, "y1": py,
                                    "x2": dx, "y2": dy,
                                })
            except Exception:
                pass

        # Compute bounding rect
        xs = [px for (px, _) in positions.values()] if positions else [0]
        ys = [py for (_, py) in positions.values()] if positions else [0]
        min_x, max_x = min(xs) - 100, max(xs) + 100
        min_y, max_y = min(ys) - 100, max(ys) + 100
        result["rect"] = (min_x, min_y, max_x - min_x, max_y - min_y)

        return result


class DependencyGraphView(QGraphicsView):
    """Interactive dependency graph for ALL systemd units.
    Built async; only unit selection teleports, no full redraw."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager: Optional[SystemdManager] = None
        self._current_unit: str = ""
        self._scene_data = None
        self._graph_built = False
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self._build_thread: Optional[GraphBuildThread] = None
        self._setup_ui()

    def _setup_ui(self):
        self.setMinimumSize(300, 200)
        self.setStyleSheet("background: palette(base); border: none;")

    def _stop_thread(self):
        """Safely stop any running build thread."""
        if self._build_thread is not None:
            t = self._build_thread
            self._build_thread = None
            if t.isRunning():
                t.quit()
                t.wait(2000)  # Wait up to 2s for clean exit

    def set_manager(self, manager: SystemdManager):
        self._manager = manager

    def set_unit(self, unit_name: str):
        """Only change position/zoom - never redraw. Fast and safe."""
        if unit_name == self._current_unit:
            return
        self._current_unit = unit_name

        if not self._graph_built:
            self.refresh()
            return

        # Just update existing node styles and teleport
        try:
            for item in list(self._scene.items()):
                if isinstance(item, QGraphicsEllipseItem):
                    tip = item.toolTip()
                    if tip == unit_name:
                        item.setPen(QPen(QColor("#e74c3c"), 3))
                        item.setBrush(QBrush(QColor("#f1948a")))
                        rect = item.sceneBoundingRect()
                        self.resetTransform()
                        self.scale(1.5, 1.5)
                        self.centerOn(rect.center())
                        item.setPen(QPen(QColor("#fff"), 3))
                        QTimer.singleShot(400, lambda it=item: self._reset_pen(it))
                    elif tip:
                        color = QColor("#3498db")
                        if ".timer" in tip: color = QColor("#9b59b6")
                        elif ".target" in tip: color = QColor("#2ecc71")
                        elif ".socket" in tip: color = QColor("#e67e22")
                        elif ".path" in tip: color = QColor("#1abc9c")
                        item.setPen(QPen(color.darker(130), 2))
                        item.setBrush(QBrush(color.lighter(180)))
        except (RuntimeError, Exception):
            pass

    def _reset_pen(self, item):
        try:
            if item and item.scene():
                item.setPen(QPen(QColor("#e74c3c"), 3))
        except RuntimeError:
            pass

    def refresh(self):
        """Async rebuild - runs in background thread, does NOT block UI."""
        self._stop_thread()

        if not self._manager:
            return

        t = GraphBuildThread(self._manager, self._current_unit)
        self._build_thread = t
        t.data_ready.connect(self._on_data_ready)
        t.finished.connect(self._on_thread_finished)
        t.start()

    def _on_thread_finished(self):
        """Thread finished signal - only null if we haven't replaced it."""
        if self._build_thread and not self._build_thread.isRunning():
            self._build_thread = None

    def _render_graph_from_data(self, data: dict):
        """Create a QGraphicsScene from collected data (main thread only)."""
        scene = QGraphicsScene()

        if data.get("error"):
            scene.addText(data["error"])
            return scene

        nodes = data.get("nodes", [])
        if not nodes:
            scene.addText("No services to display")
            return scene

        node_radius = 25
        for nd in nodes:
            color = QColor(nd["color"])
            x, y = nd["x"], nd["y"]
            
            ellipse = scene.addEllipse(
                x - node_radius, y - node_radius,
                node_radius * 2, node_radius * 2,
                QPen(color.darker(130), 2), QBrush(color.lighter(180))
            )
            if ellipse:
                ellipse.setToolTip(nd["name"])

            text = scene.addText(nd["short_name"])
            if text:
                text.setDefaultTextColor(color.darker(200))
                font = text.font()
                font.setPointSize(7)
                text.setFont(font)
                text.setPos(x - text.boundingRect().width() / 2,
                           y - text.boundingRect().height() / 2)

        for edge in data.get("edges", []):
            scene.addLine(
                edge["x1"], edge["y1"],
                edge["x2"], edge["y2"],
                QPen(QColor("#aaa"), 1, Qt.PenStyle.DotLine)
            )

        rect_data = data.get("rect")
        if rect_data:
            rx, ry, rw, rh = rect_data
            scene.setSceneRect(rx, ry, rw, rh)

        return scene

    def _on_data_ready(self, data: dict, highlight_name: str):
        """Called on main thread when data is collected. Renders scene here."""
        scene = self._render_graph_from_data(data)
        if not scene:
            return

        try:
            old_scene = self._scene
            self._scene = scene
            self.setScene(self._scene)
            
            if old_scene:
                old_scene.clear()

            rect = self._scene.sceneRect()
            if rect.width() > 0 and rect.height() > 0:
                self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
                self.scale(1.2, 1.2)

            self._graph_built = True

            if highlight_name:
                for item in self._scene.items():
                    if isinstance(item, QGraphicsEllipseItem):
                        if item.toolTip() == highlight_name:
                            item.setPen(QPen(QColor("#fff"), 3))
                            self.resetTransform()
                            self.scale(1.5, 1.5)
                            self.centerOn(item.sceneBoundingRect().center())
                            QTimer.singleShot(400, lambda it=item: self._reset_pen(it))
                            break
        except Exception:
            pass

    def clear(self):
        """Clear the graph without blocking."""
        self._current_unit = ""
        self._graph_built = False
        self._stop_thread()
        try:
            s = QGraphicsScene()
            s.addText("No unit selected")
            old = self._scene
            self._scene = s
            self.setScene(self._scene)
            if old:
                old.clear()
        except Exception:
            pass

    def closeEvent(self, event):
        """Ensure thread stops when widget closes."""
        self._stop_thread()
        super().closeEvent(event)

    def wheelEvent(self, event):
        factor = 1.15
        if event.angleDelta().y() < 0:
            factor = 1.0 / factor
        self.scale(factor, factor)


# ─── File Browser ──────────────────────────────────────────────────

class UnitFileBrowser(QWidget):
    """Browser for systemd unit files on the disk."""
    unit_selected = pyqtSignal(str)
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager: Optional[SystemdManager] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        search_layout = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter units...")
        self._search_edit.textChanged.connect(self._filter_units)
        search_layout.addWidget(self._search_edit)

        self._scope_combo = QComboBox()
        self._scope_combo.addItems(["system", "user"])
        self._scope_combo.currentTextChanged.connect(self._on_scope_changed)
        search_layout.addWidget(self._scope_combo)

        self._hide_devices_cb = QCheckBox("Hide .device/.mount/.slice")
        self._hide_devices_cb.setChecked(True)
        self._hide_devices_cb.setToolTip("Hide .device, .mount, and .slice units from the list")
        self._hide_devices_cb.stateChanged.connect(lambda: self._update_tree(self._search_edit.text() or ""))
        search_layout.addWidget(self._hide_devices_cb)

        self._hide_nf_cb = QCheckBox("Hide not-found")
        self._hide_nf_cb.setChecked(True)
        self._hide_nf_cb.setToolTip("Hide units in 'not-found' state")
        self._hide_nf_cb.stateChanged.connect(lambda: self._update_tree(self._search_edit.text() or ""))
        search_layout.addWidget(self._hide_nf_cb)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip("Refresh unit list")
        self._refresh_btn.clicked.connect(self.refresh_units)
        search_layout.addWidget(self._refresh_btn)
        layout.addLayout(search_layout)

        self._unit_tree = QTreeWidget()
        self._unit_tree.setHeaderLabels(["Name", "State", "Description"])
        self._unit_tree.setRootIsDecorated(False)
        self._unit_tree.setAlternatingRowColors(True)
        self._unit_tree.setSortingEnabled(True)
        self._unit_tree.itemClicked.connect(self._on_unit_clicked)
        self._unit_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._unit_tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._unit_tree, 1)

        self._all_units: List[Dict] = []

    def set_manager(self, manager: SystemdManager):
        self._manager = manager
        self.refresh_units()

    def refresh_units(self):
        if not self._manager:
            return
        self._all_units = self._manager.list_units()
        self._update_tree()
    
    @property
    def _hide_device_mount(self):
        """Check if device/mount units should be hidden."""
        return not hasattr(self, '_show_hidden') or not self._show_hidden

    @property
    def _hide_not_found(self):
        """Check if not-found units should be hidden."""
        return True  # Always hide not-found by default

    def _update_tree(self, filter_text: str = ""):
        self._unit_tree.clear()
        hide_devices = self._hide_devices_cb.isChecked()
        hide_nf = self._hide_nf_cb.isChecked()
        
        for unit in self._all_units:
            name = unit.get("name", "")
            
            # Filter by search text
            if filter_text and filter_text.lower() not in name.lower():
                continue
            
            # Hide .device, .mount, and .slice units
            if hide_devices and (name.endswith(".device") or name.endswith(".mount") or name.endswith(".slice")):
                continue
            
            # Hide not-found units (the active/state field is "not-found")
            active_state = unit.get("active", "")
            if hide_nf and active_state == "not-found":
                continue
            
            item = QTreeWidgetItem()
            item.setText(0, name)
            item.setText(1, unit.get("active", ""))
            item.setText(2, unit.get("description", ""))
            item.setData(0, Qt.ItemDataRole.UserRole, unit)

            active = unit.get("active", "")
            if active == "active":
                item.setForeground(1, QColor("#27ae60"))
            elif active == "failed":
                item.setForeground(1, QColor("#e74c3c"))
            elif active in ("inactive", "dead"):
                item.setForeground(1, QColor("#888"))

            self._unit_tree.addTopLevelItem(item)

    def _filter_units(self, text: str):
        self._update_tree(text)

    def _on_scope_changed(self, scope: str):
        if self._manager:
            self._manager.scope = scope
            self._manager.base_path = Path(
                "/etc/systemd/system" if scope == "system"
                else os.path.expanduser("~/.config/systemd/user")
            )
            self._manager._systemctl_base = ["systemctl"] if scope == "system" else ["systemctl", "--user"]
            self.refresh_units()

    def _on_unit_clicked(self, item, column):
        unit_data = item.data(0, Qt.ItemDataRole.UserRole)
        if unit_data:
            self.unit_selected.emit(unit_data.get("name", ""))

    def _show_context_menu(self, pos):
        item = self._unit_tree.itemAt(pos)
        if not item:
            return
        unit_data = item.data(0, Qt.ItemDataRole.UserRole)
        if not unit_data:
            return
        name = unit_data.get("name", "")

        menu = QMenu(self)
        menu.addAction("Edit in editor", lambda: self.unit_selected.emit(name))
        if self._manager:
            menu.addSeparator()
            menu.addAction("Start", lambda: self._manager.start_unit(name))
            menu.addAction("Stop", lambda: self._manager.stop_unit(name))
            menu.addAction("Restart", lambda: self._manager.restart_unit(name))
            menu.addAction("Enable", lambda: self._manager.enable_unit(name))
            menu.addAction("Disable", lambda: self._manager.disable_unit(name))
            menu.addSeparator()
            menu.addAction("View Dependency Tree", lambda: self._show_dep_tree(name))
        menu.exec(self._unit_tree.viewport().mapToGlobal(pos))

    def _show_dep_tree(self, name):
        """Show the dependency tree dialog."""
        if self._manager and name:
            top = self.window()
            dialog = DependencyTreeDialog(self._manager, name, top)
            dialog.open_unit.connect(top._on_unit_selected)
            dialog.exec()


# ─── Dependency Tree Dialog ──────────────────────────────────────────

class DependencyTreeDialog(QDialog):
    """Dialog showing a tree of all dependencies for a unit."""
    
    open_unit = pyqtSignal(str)

    def __init__(self, manager: SystemdManager, unit_name: str, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._unit_name = unit_name
        self.setWindowTitle("Dependency Tree: " + unit_name)
        self.resize(600, 500)
        self._setup_ui()
        self._build_tree()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Header
        header = QLabel("<b>Dependency Tree for:</b> " + self._unit_name)
        layout.addWidget(header)
        
        # Tree widget
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Unit", "Relation"])
        self._tree.setAlternatingRowColors(True)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._tree, 1)
        
        # Info label
        self._info_label = QLabel("Double-click a unit to open it in the editor")
        self._info_label.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(self._info_label)
        
        # Close button
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.close)
        layout.addWidget(btn_box)

    def _build_tree(self):
        self._tree.clear()
        try:
            tree = self._manager.get_all_dependencies(self._unit_name, max_depth=3)
        except Exception:
            self._tree.addTopLevelItem(QTreeWidgetItem(["Could not load dependencies", ""]))
            return
            
        visited = set()
        def _add_children(parent_item, name, depth=0):
            if name in visited or depth > 3:
                return
            visited.add(name)
            
            deps = tree.get(name, [])
            for dep in deps:
                dep_name = dep.get("name", "")
                dep_type = dep.get("type", "unknown")
                item = QTreeWidgetItem([dep_name, dep_type])
                
                # Color code by relation type
                if dep_type == "Requires":
                    item.setForeground(0, QColor("#e74c3c"))
                elif dep_type == "Wants":
                    item.setForeground(0, QColor("#f39c12"))
                elif dep_type == "Before":
                    item.setForeground(0, QColor("#3498db"))
                elif dep_type == "After":
                    item.setForeground(0, QColor("#2ecc71"))
                
                item.setData(0, Qt.ItemDataRole.UserRole, dep_name)
                
                if parent_item:
                    parent_item.addChild(item)
                else:
                    self._tree.addTopLevelItem(item)
                
                _add_children(item, dep_name, depth + 1)
        
        root = QTreeWidgetItem([self._unit_name, "root"])
        root.setForeground(0, QColor("#e74c3c"))
        root.setData(0, Qt.ItemDataRole.UserRole, self._unit_name)
        self._tree.addTopLevelItem(root)
        _add_children(root, self._unit_name)
        self._tree.expandAll()

    def _on_item_double_clicked(self, item, column):
        unit_name = item.data(0, Qt.ItemDataRole.UserRole)
        if unit_name:
            self.open_unit.emit(unit_name)
            self.close()


# ─── Raw Text Editor Tab ───────────────────────────────────────────

class RawEditorWidget(QWidget):
    """Raw text editor for the unit file content."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._editor = QPlainTextEdit()
        self._editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        fm = QFontMetricsF(self._editor.font())
        self._editor.setTabStopDistance(fm.horizontalAdvance(" ") * 4)
        self._highlighter = SystemdHighlighter(self._editor.document())

        layout.addWidget(self._editor)

    def set_content(self, content: str):
        self._editor.setPlainText(content)

    def get_content(self) -> str:
        return self._editor.toPlainText()

    def clear(self):
        self._editor.clear()


# ─── Main Window ───────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self._manager = SystemdManager()
        self._current_unit_name: str = ""
        self._setup_ui()
        self._setup_menu()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self._refresh_timer.start(10000)

        self.setWindowTitle("SysdGen - Systemd Unit Generator")
        self.resize(1400, 900)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        h_splitter = QSplitter(Qt.Orientation.Horizontal)

        self._browser = UnitFileBrowser()
        self._browser.set_manager(self._manager)
        self._browser.unit_selected.connect(self._on_unit_selected)
        h_splitter.addWidget(self._browser)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._editor_tabs = QTabWidget()
        self._editor_tabs.setDocumentMode(True)

        self._form_editor = UnitEditorWidget()
        self._editor_tabs.addTab(self._form_editor, "Form Editor")

        self._raw_editor = RawEditorWidget()
        self._editor_tabs.addTab(self._raw_editor, "Raw Text")

        self._editor_tabs.currentChanged.connect(self._on_editor_tab_changed)
        right_layout.addWidget(self._editor_tabs, 1)

        bottom_tabs = QTabWidget()
        bottom_tabs.setDocumentMode(True)

        self._status_panel = StatusPanel()
        self._status_panel.set_manager(self._manager)
        self._status_panel.unit_action.connect(self._on_unit_action)
        bottom_tabs.addTab(self._status_panel, "Status")

        self._journal_view = JournalLogView()
        self._journal_view.set_manager(self._manager)
        bottom_tabs.addTab(self._journal_view, "Journal Log")

        self._graph_view = DependencyGraphView()
        self._graph_view.set_manager(self._manager)
        bottom_tabs.addTab(self._graph_view, "Dependency Graph")

        right_layout.addWidget(bottom_tabs, 1)

        h_splitter.addWidget(right_widget)
        h_splitter.setSizes([300, 1100])

        main_layout.addWidget(h_splitter)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_label = QLabel("Ready")
        self._status_bar.addWidget(self._status_label)

    def _setup_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        act = QAction("&New Unit", self)
        act.triggered.connect(self._new_unit)
        act.setShortcut("Ctrl+N")
        file_menu.addAction(act)
        
        act = QAction("&Open File...", self)
        act.triggered.connect(self._open_file)
        act.setShortcut("Ctrl+O")
        file_menu.addAction(act)
        
        act = QAction("&Save", self)
        act.triggered.connect(self._save_unit)
        act.setShortcut("Ctrl+S")
        file_menu.addAction(act)
        
        act = QAction("Save &As...", self)
        act.triggered.connect(self._save_as)
        act.setShortcut("Ctrl+Shift+S")
        file_menu.addAction(act)
        
        file_menu.addSeparator()
        
        act = QAction("E&xit", self)
        act.triggered.connect(self.close)
        act.setShortcut("Ctrl+Q")
        file_menu.addAction(act)

        actions_menu = menubar.addMenu("&Actions")
        actions_menu.addAction("&Start Unit", self._action_start)
        actions_menu.addAction("S&top Unit", self._action_stop)
        actions_menu.addAction("&Restart Unit", self._action_restart)
        actions_menu.addAction("&Enable Unit", self._action_enable)
        actions_menu.addAction("&Disable Unit", self._action_disable)
        actions_menu.addSeparator()
        actions_menu.addAction("Daemon &Reload", self._action_daemon_reload)

        view_menu = menubar.addMenu("&View")
        act = QAction("&Refresh Units", self)
        act.triggered.connect(self._browser.refresh_units)
        act.setShortcut("F5")
        view_menu.addAction(act)
        view_menu.addSeparator()
        view_menu.addAction("&System scope", lambda: self._browser._scope_combo.setCurrentIndex(0))
        view_menu.addAction("&User scope", lambda: self._browser._scope_combo.setCurrentIndex(1))

        help_menu = menubar.addMenu("&Help")
        help_menu.addAction("&About", self._show_about)

    def _new_unit(self):
        self._form_editor.set_unit(UnitFile(filename="new.service"))
        self._current_unit_name = "new.service"
        self._status_panel.clear_unit()
        self._journal_view.clear_unit()
        self._graph_view.clear()
        self._raw_editor.clear()
        self._status_label.setText("New unit created")

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Unit File",
            str(self._manager.base_path),
            "Systemd units (*.service *.socket *.timer *.path *.mount *.automount *.swap *.target *.device *.scope *.slice);;All Files (*)"
        )
        if not path:
            return
        self._load_file(path)

    def _load_file(self, path: str):
        unit = self._manager.load_unit_file_from_path(path)
        if unit:
            self._form_editor.set_unit(unit)
            self._current_unit_name = unit.filename
            self._raw_editor.set_content(unit.to_unit_file_content())
            self._status_panel.set_unit(unit.filename)
            self._journal_view.set_unit(unit.filename)
            self._graph_view.set_unit(unit.filename)
            self._status_label.setText("Loaded: " + path)
            self._editor_tabs.setCurrentIndex(0)
            self._browser.refresh_units()
        else:
            QMessageBox.critical(self, "Error", "Failed to load:\n" + path)

    def _save_unit(self):
        self._form_editor._on_save()
        if self._raw_editor:
            self._raw_editor.set_content(self._form_editor.get_unit_file_content())

    def _save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Unit File As",
            str(self._manager.base_path),
            "Systemd units (*.service *.socket *.timer *.path *.mount *.automount *.swap *.target *.device *.scope *.slice);;All Files (*)"
        )
        if not path:
            return
        self._form_editor.save_editor_to_unit()
        unit = self._form_editor.get_unit()
        if unit:
            if self._manager.save_unit_file(unit, path):
                unit.filepath = path
                self._current_unit_name = unit.filename
                self._status_label.setText("Saved: " + path)
                self._browser.refresh_units()

    def _on_unit_selected(self, unit_name: str):
        unit = self._manager.load_unit_file(unit_name)
        if unit:
            self._form_editor.set_unit(unit)
            self._current_unit_name = unit.filename
            self._raw_editor.set_content(unit.to_unit_file_content())
            self._status_panel.set_unit(unit.filename)
            self._journal_view.set_unit(unit.filename)
            self._graph_view.set_unit(unit.filename)
            self._status_label.setText("Loaded: " + unit.filename)
            self._editor_tabs.setCurrentIndex(0)
        else:
            QMessageBox.warning(self, "Load Error",
                              "Could not load unit: " + unit_name)

    def _on_editor_tab_changed(self, index: int):
        if index == 0:
            raw = self._raw_editor.get_content()
            if raw.strip():
                unit = UnitFile.from_unit_file_content(raw)
                unit.filename = self._form_editor._filename_edit.text() or "unit.service"
                self._form_editor.set_unit(unit)
        elif index == 1:
            content = self._form_editor.get_unit_file_content()
            self._raw_editor.set_content(content)

    def _on_unit_action(self, action: str, unit_name: str):
        self._status_label.setText(action.title() + " " + unit_name + "...")

    def _auto_refresh(self):
        if self._current_unit_name:
            self._status_panel._refresh_status()

    def _action_start(self):
        if self._current_unit_name:
            self._status_panel._do_action("start")

    def _action_stop(self):
        if self._current_unit_name:
            self._status_panel._do_action("stop")

    def _action_restart(self):
        if self._current_unit_name:
            self._status_panel._do_action("restart")

    def _action_enable(self):
        if self._current_unit_name:
            self._status_panel._do_action("enable")

    def _action_disable(self):
        if self._current_unit_name:
            self._status_panel._do_action("disable")

    def _action_daemon_reload(self):
        self._status_panel._do_action("daemon-reload")

    def _show_about(self):
        msg = (
            "<h2>SysdGen - Systemd Unit Generator</h2>"
            "<p>A comprehensive GUI tool for creating, editing, and managing "
            "systemd unit files.</p>"
            "<p>Features:</p>"
            "<ul>"
            "<li>Complete editor for ALL systemd options across all unit types</li>"
            "<li>Live journalctl log viewer</li>"
            "<li>Unit status and control (start/stop/restart/enable/disable)</li>"
            "<li>Dependency graph visualization</li>"
            "<li>KDE-themed interface</li>"
            "</ul>"
        )
        QMessageBox.about(self, "About SysdGen", msg)

    def closeEvent(self, event):
        self._journal_view.closeEvent(event)
        self._refresh_timer.stop()
        super().closeEvent(event)


# ─── Entry point ───────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    apply_kde_theme(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()