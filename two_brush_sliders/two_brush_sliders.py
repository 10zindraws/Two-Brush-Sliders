# Two Brush Sliders - Krita Plugin
# Provides a docker with two sliders for controlling brush hardness and size
# Uses signals-only detection with action monitoring (no timer polling)

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Krita, Preset
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QDoubleSpinBox, QSizePolicy
from PyQt5.QtCore import QTimer, QStandardPaths, Qt, QRect
from PyQt5.QtGui import QPainter, QPalette, QPen
import xml.etree.ElementTree as ET
import json
from pathlib import Path


class SliderSpinBox(QDoubleSpinBox):
    """Custom QDoubleSpinBox that looks and behaves like Krita's KisDoubleSliderSpinBox."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slider_dragging = False
        self._slider_value = 0.0
        self.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.setAlignment(Qt.AlignCenter)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(self._min_slider_height())
        # Make text non-selectable and widget read-only to prevent text interaction
        self.setReadOnly(True)
        self.setFocusPolicy(Qt.ClickFocus)
        # Hide the internal line edit so custom paint text is the only label and
        # mouse drags go to the slider instead of selecting text.
        line_edit = self.lineEdit()
        line_edit.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        line_edit.setVisible(False)
        # Disable frame to have full control over painting
        self.setFrame(False)
        # Set pointer cursor for slider interaction
        self.setCursor(Qt.PointingHandCursor)

    def _min_slider_height(self):
        return max(14, self.fontMetrics().height() + 6)

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(min(size.height(), self._min_slider_height()))
        return size

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        size.setHeight(self._min_slider_height())
        return size

    def paintEvent(self, event):
        """Custom paint to show slider-like appearance with filled portion."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Get widget rect
        rect = self.rect()

        # Draw background
        bg_color = self.palette().color(QPalette.Base)
        painter.fillRect(rect, bg_color)

        # Calculate fill width based on value
        value_range = self.maximum() - self.minimum()
        if value_range > 0:
            fill_ratio = (self.value() - self.minimum()) / value_range
            fill_width = int(rect.width() * fill_ratio)

            # Draw filled portion
            fill_rect = QRect(rect.left(), rect.top(), fill_width, rect.height())
            fill_color = self.palette().color(QPalette.Highlight).lighter(150)
            painter.fillRect(fill_rect, fill_color)

        # Draw border
        border_color = self.palette().color(QPalette.Mid)
        painter.setPen(QPen(border_color, 1))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        # Draw text
        text = self.textFromValue(self.value())
        text_color = self.palette().color(QPalette.Text)
        painter.setPen(text_color)
        painter.drawText(rect, Qt.AlignCenter, text)

    def mousePressEvent(self, event):
        """Start slider dragging on left click."""
        if event.button() == Qt.LeftButton:
            self._slider_dragging = True
            self._updateValueFromMouse(event.pos())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Update value while dragging."""
        if self._slider_dragging:
            self._updateValueFromMouse(event.pos())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Stop slider dragging."""
        if event.button() == Qt.LeftButton and self._slider_dragging:
            self._slider_dragging = False
            self._updateValueFromMouse(event.pos())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _updateValueFromMouse(self, pos):
        """Calculate and set value from mouse position."""
        rect = self.rect()
        ratio = max(0.0, min(1.0, pos.x() / rect.width()))
        value_range = self.maximum() - self.minimum()
        new_value = self.minimum() + (ratio * value_range)

        # Round to single step
        steps = round(new_value / self.singleStep())
        new_value = steps * self.singleStep()

        self.setValue(new_value)

    def textFromValue(self, value):
        """Format the display text with prefix and suffix."""
        return f"{self.prefix()}{int(value)}{self.suffix()}"

    def keyPressEvent(self, event):
        """Override key press to prevent text editing."""
        # Allow arrow keys, page up/down, home/end for value changes
        if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
                           Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End):
            super().keyPressEvent(event)
        else:
            # Ignore other keys to prevent text editing
            event.ignore()

    def focusInEvent(self, event):
        """Override focus in to prevent text selection."""
        # Accept focus but don't select text
        QWidget.focusInEvent(self, event)

    def contextMenuEvent(self, event):
        """Override context menu to prevent copy/paste menu."""
        event.ignore()


class TwoBrushSlidersDocker(DockWidget):
    """Docker widget with two sliders for brush hardness and size control."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Two Brush Sliders")

        # Initialize state
        self.max_brush_size = 1000  # Default max
        self.last_brush_size = None
        self.last_hardness = None
        self._pending_config_save = False

        # Create main widget
        main_widget = QWidget()
        self.setWidget(main_widget)

        # Setup layout
        layout = QVBoxLayout()
        main_widget.setLayout(layout)

        # Load config first to get max_brush_size
        self.load_config()

        # Create hardness slider
        self.hardness_slider = SliderSpinBox()
        self.hardness_slider.setRange(0, 100)
        self.hardness_slider.setSingleStep(1)
        self.hardness_slider.setPrefix("Hardness: ")
        self.hardness_slider.setSuffix("%")

        # Create size slider
        self.size_slider = SliderSpinBox()
        self.size_slider.setRange(1, self.max_brush_size)
        self.size_slider.setSingleStep(1)
        self.size_slider.setPrefix("Brush Size: ")
        self.size_slider.setSuffix(" px")

        layout.addWidget(self.hardness_slider, 1)
        layout.addWidget(self.size_slider, 1)

        # Setup signals
        self.setup_signals()

        # Initialize slider values from current brush
        self.update_from_current_brush()

    def canvasChanged(self, canvas):
        """Required override for DockWidget - called when canvas changes."""
        # Update sliders when canvas changes
        if canvas:
            self.update_from_current_brush()

    def load_config(self):
        """Load configuration from disk."""
        config_path = self.get_config_path()
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    loaded_max = config.get("max_brush_size", 1000)
                    # Only use loaded max if it's reasonable and was likely set by detection
                    # Reset to 1000 if current brush is smaller
                    self.max_brush_size = max(100, min(10000, loaded_max))
            except (json.JSONDecodeError, IOError) as e:
                print(f"Two Brush Sliders: Error loading config: {e}")
                self.max_brush_size = 1000

        # After initial load, check current brush size and adjust max accordingly
        # This ensures max is appropriate for current brush, not leftover from previous session
        QTimer.singleShot(500, self._validate_max_brush_size)

    def save_config(self):
        """Save configuration to disk with debouncing."""
        if not self._pending_config_save:
            self._pending_config_save = True
            QTimer.singleShot(500, self._do_save_config)

    def _do_save_config(self):
        """Actually write config to disk."""
        self._pending_config_save = False
        config_path = self.get_config_path()
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config = {"max_brush_size": self.max_brush_size}
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
        except IOError as e:
            print(f"Two Brush Sliders: Error saving config: {e}")

    def get_config_path(self):
        """Get the configuration file path."""
        # Get Krita's config directory
        config_location = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
        return Path(config_location) / "krita" / "two_brush_sliders_config.json"

    def setup_signals(self):
        """Connect to Krita signals and slider signals."""
        # Connect slider value changes to brush updates
        self.hardness_slider.valueChanged.connect(self.on_hardness_slider_changed)
        self.size_slider.valueChanged.connect(self.on_size_slider_changed)

        # Connect to Krita signals
        app = Krita.instance()
        notifier = app.notifier()

        # Window created signal (deferred setup)
        notifier.windowCreated.connect(self._on_window_created)

        # Resource changed signal
        if hasattr(notifier, 'resourceChanged'):
            notifier.resourceChanged.connect(self._on_resource_changed)

        # Try to connect window signals now if window exists
        if app.activeWindow():
            QTimer.singleShot(100, self._connect_window_signals)

        # Discover and connect to brush size actions
        QTimer.singleShot(200, self._discover_brush_size_actions)

    def _on_window_created(self):
        """Handle window creation - deferred signal setup."""
        QTimer.singleShot(100, self._connect_window_signals)

    def _connect_window_signals(self):
        """Connect to window and view signals."""
        app = Krita.instance()
        window = app.activeWindow()
        if not window:
            return

        # Connect to view change signal
        if hasattr(window, 'activeViewChanged'):
            try:
                window.activeViewChanged.connect(self._on_view_changed)
            except:
                pass  # Signal may already be connected

    def _on_resource_changed(self, resource_type, resource_name):
        """Handle resource changes (preset changes)."""
        if resource_type in ("preset", "brushpreset"):
            self.update_from_current_brush()

    def _on_view_changed(self):
        """Handle view changes."""
        self.update_from_current_brush()

    def _discover_brush_size_actions(self):
        """Discover and connect to brush size increase/decrease actions."""
        app = Krita.instance()

        # List of possible action name patterns
        candidates = [
            ("increase_brush_size", "decrease_brush_size"),
            ("brush_size_increase", "brush_size_decrease"),
            ("IncreaseBrushSize", "DecreaseBrushSize"),
            ("increase_brushsize", "decrease_brushsize"),
        ]

        for inc_name, dec_name in candidates:
            inc_action = app.action(inc_name)
            dec_action = app.action(dec_name)

            if inc_action and dec_action:
                # Found the actions!
                inc_action.triggered.connect(self._on_brush_size_action_triggered)
                dec_action.triggered.connect(self._on_brush_size_action_triggered)
                print(f"Two Brush Sliders: Connected to actions {inc_name}/{dec_name}")
                return True

        # Fallback: Print all actions for debugging
        print("Two Brush Sliders: Could not find brush size actions. Available brush-related actions:")
        all_actions = app.actions()
        for action in all_actions:
            name = action.objectName()
            text = action.text()
            if "brush" in name.lower() or "size" in name.lower():
                print(f"  {name}: {text}")

        return False

    def _on_brush_size_action_triggered(self):
        """Handle brush size increase/decrease action triggered with debouncing."""
        # Cancel pending debounce timer if exists
        if hasattr(self, '_action_debounce_timer') and self._action_debounce_timer.isActive():
            self._action_debounce_timer.stop()

        # Create or reuse debounce timer
        if not hasattr(self, '_action_debounce_timer'):
            self._action_debounce_timer = QTimer()
            self._action_debounce_timer.setSingleShot(True)
            self._action_debounce_timer.timeout.connect(self._update_size_from_action)

        # Start 100ms debounce
        self._action_debounce_timer.start(100)

    def _update_size_from_action(self):
        """Update size slider after debounce period."""
        view = self._get_active_view()
        if view:
            size = view.brushSize()
            self._update_size_slider(size)

    def _validate_max_brush_size(self):
        """Validate and potentially reset max brush size based on current brush."""
        view = self._get_active_view()
        if not view:
            return

        current_size = view.brushSize()

        # If current brush is much smaller than stored max, reset to 1000
        # This handles case where previous session had large brush but current doesn't need it
        if current_size <= 1000 and self.max_brush_size > 1000:
            self.max_brush_size = 1000
            self.size_slider.setMaximum(self.max_brush_size)
            self.save_config()
            print(f"Two Brush Sliders: Reset max brush size to 1000px")
        # If current brush is larger than stored max, expand it
        elif current_size > self.max_brush_size:
            new_max = min(10000, max(1000, int(current_size)))
            self.max_brush_size = new_max
            self.size_slider.setMaximum(self.max_brush_size)
            self.save_config()
            print(f"Two Brush Sliders: Expanded max brush size to {new_max}px")

    def update_from_current_brush(self):
        """Update both sliders from current brush state."""
        view = self._get_active_view()
        if not view:
            return

        # Update size
        size = view.brushSize()
        self._update_size_slider(size)

        # Update hardness
        hardness_value = self._get_brush_hardness()
        if hardness_value is not None:
            self._update_hardness_slider(hardness_value)

    def _update_size_slider(self, size):
        """Update size slider value with signal blocking."""
        if size is None:
            return

        # Check if we need to expand max size
        if size > self.max_brush_size:
            new_max = min(10000, max(100, int(size)))
            if new_max != self.max_brush_size:
                self.max_brush_size = new_max
                self.size_slider.setMaximum(self.max_brush_size)
                self.save_config()

        # Update slider if value changed
        clamped_size = max(1, min(self.max_brush_size, size))
        if self.last_brush_size != clamped_size:
            self.last_brush_size = clamped_size
            self.size_slider.blockSignals(True)
            self.size_slider.setValue(int(clamped_size))
            self.size_slider.blockSignals(False)

    def _update_hardness_slider(self, hardness_value):
        """Update hardness slider value with signal blocking."""
        if hardness_value is None:
            return

        # Convert from 0.0-1.0 to 0-100 for display
        display_value = int(hardness_value * 100.0)

        if self.last_hardness != display_value:
            self.last_hardness = display_value
            self.hardness_slider.blockSignals(True)
            self.hardness_slider.setValue(display_value)
            self.hardness_slider.blockSignals(False)

    def on_hardness_slider_changed(self, value):
        """Handle hardness slider value change."""
        # Convert from 0-100 display to 0.0-1.0 internal
        internal_value = value / 100.0
        self._set_brush_hardness(internal_value)
        self.last_hardness = value

    def on_size_slider_changed(self, value):
        """Handle size slider value change."""
        view = self._get_active_view()
        if view:
            view.setBrushSize(float(value))
            self.last_brush_size = value

            # Note: Max expansion only happens in _update_size_slider()
            # when detecting external changes, not from user dragging slider

    def _get_active_view(self):
        """Get the active view safely."""
        app = Krita.instance()
        if not app:
            return None
        window = app.activeWindow()
        if not window:
            return None
        return window.activeView()

    def _get_brush_hardness(self):
        """Get current brush hardness (0.0-1.0)."""
        view = self._get_active_view()
        if not view:
            return None

        try:
            preset = Preset(view.currentBrushPreset())
            preset_xml_string = preset.toXML()
            preset_tree = ET.fromstring(preset_xml_string)

            for param in preset_tree.findall('param'):
                if param.get('name') == "brush_definition":
                    if not param.text:
                        continue
                    brushdef = ET.fromstring(param.text)
                    if brushdef.get("type") != "auto_brush":
                        continue
                    brushopt = self._find_mask_generator(brushdef)
                    if brushopt is None:
                        continue
                    hfade = brushopt.get('hfade')
                    if hfade is None:
                        hfade = brushopt.get('vfade')
                    if hfade is None:
                        continue
                    return float(hfade)
        except Exception:
            # Silently handle errors for brushes without hardness
            pass

        return None

    def _find_mask_generator(self, brushdef):
        """Locate the MaskGenerator node for auto brushes."""
        brushopt = brushdef.find('MaskGenerator')
        if brushopt is not None:
            return brushopt
        brushopt = brushdef.find('.//MaskGenerator')
        if brushopt is not None:
            return brushopt
        for child in brushdef.iter():
            if child.tag.endswith('MaskGenerator'):
                return child
        return None

    def _set_brush_hardness(self, value):
        """Set brush hardness (0.0-1.0)."""
        view = self._get_active_view()
        if not view:
            return

        try:
            preset = Preset(view.currentBrushPreset())
            preset_xml_string = preset.toXML()
            preset_tree = ET.fromstring(preset_xml_string)
            updated = False

            for param in preset_tree.findall('param'):
                if param.get('name') == "brush_definition":
                    if not param.text:
                        continue
                    brushdef = ET.fromstring(param.text)
                    if brushdef.get("type") != "auto_brush":
                        continue
                    brushopt = self._find_mask_generator(brushdef)
                    if brushopt is None:
                        continue
                    # Set both horizontal and vertical fade
                    clamped_value = max(0.0, min(1.0, float(value)))
                    brushopt.set('hfade', str(clamped_value))
                    brushopt.set('vfade', str(clamped_value))

                    # Update the XML
                    xmlbrushdef = ET.tostring(brushdef, encoding="unicode")
                    param.text = xmlbrushdef
                    updated = True
                    break

            if updated:
                # Apply the changes
                preset_xml_string = ET.tostring(preset_tree, encoding="unicode")
                preset.fromXML(preset_xml_string)
        except Exception as e:
            print(f"Two Brush Sliders: Error setting hardness: {e}")


# Module-level registration
instance = Krita.instance()
dock_widget_factory = DockWidgetFactory(
    'two_brush_sliders_docker',
    DockWidgetFactoryBase.DockRight,
    TwoBrushSlidersDocker
)
instance.addDockWidgetFactory(dock_widget_factory)
