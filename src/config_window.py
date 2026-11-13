#!/usr/bin/env python3
"""
GTK configuration window for post-transcription settings.

Opened from the tray icon menu to choose what happens after a recording is transcribed.
"""

import json
import os
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def _load_config():
    """Load settings from config.json, returning defaults on any error."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def _save_config(data):
    """Write settings dict to config.json."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)
        f.write("\n")


def get_post_transcription_action():
    """Return the configured action: 'clipboard' or 'cmd'."""
    config = _load_config()
    return config.get("post_transcription_action_type", "clipboard")


def get_post_transcription_command():
    """Return the command string when action is 'cmd'."""
    config = _load_config()
    return config.get("post_transcription_cmd", "")


class ConfigWindow(Gtk.Window):
    """Modal dialog to configure post-transcription behaviour."""

    def __init__(self, parent=None):
        super().__init__(title="Post-Transcription Settings")
        self.set_default_size(580, 220)
        self.set_resizable(False)
        self.set_modal(True)
        self.set_transient_for(parent)
        self.set_destroy_with_parent(True)

        # Load current values
        current_action = get_post_transcription_action()
        current_command = get_post_transcription_command()

        # ---- main box ----
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        self.add(box)

        label = Gtk.Label(label="When a recording is transcribed:")
        label.set_xalign(0)
        box.add(label)

        # Clipboard radio
        self.radio_clipboard = Gtk.RadioButton.new_with_label_from_widget(
            None, "Copy to clipboard"
        )
        self.radio_clipboard.set_active(current_action == "clipboard")
        box.add(self.radio_clipboard)

        # Custom command radio
        self.radio_command = Gtk.RadioButton.new_with_label_from_widget(
            self.radio_clipboard, "Run a custom command"
        )
        self.radio_command.set_active(current_action == "cmd")
        box.add(self.radio_command)

        # Command entry (always visible, multiline)
        self.cmd_buffer = Gtk.TextBuffer()
        self.cmd_buffer.set_text(current_command)
        self.cmd_view = Gtk.TextView(buffer=self.cmd_buffer)
        self.cmd_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.cmd_view.set_size_request(-1, 80)
        self.cmd_view.set_hexpand(True)
        self.cmd_view.set_vexpand(True)
        self.cmd_view.set_tooltip_text(
            'Use $TEXT as a placeholder for the transcribed text.\n'
            "The command is run via /bin/sh -c."
        )
        box.add(self.cmd_view)

        # ---- buttons ----
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.END)
        box.add(btn_box)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda *_: self.destroy())
        btn_box.add(cancel_btn)

        ok_btn = Gtk.Button(label="Save")
        ok_btn.get_style_context().add_class("suggested-action")
        ok_btn.set_can_default(False)
        ok_btn.connect("clicked", self._on_save)
        btn_box.add(ok_btn)

        # Close on Escape key
        self.connect("key-press-event", self._on_key_press)

    def _on_key_press(self, _widget, event):
        from gi.repository import Gdk
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False

    def _on_save(self, _btn):
        action = "clipboard" if self.radio_clipboard.get_active() else "cmd"
        data = {"post_transcription_action_type": action}
        start, end = self.cmd_buffer.get_bounds()
        cmd = self.cmd_buffer.get_text(start, end, True).strip()
        if cmd:
            data["post_transcription_cmd"] = cmd
        _save_config(data)
        self.destroy()


# Global reference to the current config window (so app.py can close it on quit)
_config_window_ref = None


def show_config_window(parent_window=None):
    """Show the config window on the GTK main thread."""

    def _create():
        global _config_window_ref
        win = ConfigWindow(parent_window)
        _config_window_ref = win
        win.show_all()

    GLib.idle_add(_create)
