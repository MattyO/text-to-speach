#!/usr/bin/env python3
"""
GtkApp - Display last recording text and send to active opencode session.

Usage:
    python gtk_app.py
"""

import os
import sys
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Pango, GLib

import requests
import threading

# ── Configuration ──────────────────────────────────────────────
BASE_URL = "http://127.0.0.1:49374"
AUTH = ("opencode", "fqUNPvqO_waOJrho0uBNBblChK1ERBiUhvTp2KHn_js")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


# ── State ──────────────────────────────────────────────────────
class AppState:
    def __init__(self):
        self.sessions = []
        self.selected_index = 0
        self.last_recording_text = ""
        self.last_recording_file = ""
        self.send_result = "Not sent"
        self._lock = threading.Lock()


state = AppState()


# ── Helpers ────────────────────────────────────────────────────
def get_last_recording_text():
    """Get the text content of the most recent recording file."""
    try:
        txt_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".txt")]
        if not txt_files:
            return "", ""
        txt_files.sort()
        last_file = txt_files[-1]
        last_path = os.path.join(DATA_DIR, last_file)
        with open(last_path, "r") as f:
            return f.read().strip(), last_file
    except Exception as e:
        return f"Error reading recording: {e}", ""


def get_active_sessions():
    """Fetch active (busy) opencode sessions from the API."""
    try:
        resp = requests.get(f"{BASE_URL}/api/session", auth=AUTH, timeout=5)
        if resp.status_code != 200:
            return []
        data = resp.json()
        sessions = data.get("data", [])
        # Filter for busy/active sessions (busy is a non-zero timestamp)
        active = [
            s for s in sessions
            if s.get("time", {}).get("busy")
        ]
        active.sort(key=lambda s: s["time"]["updated"], reverse=True)
        return active
    except Exception:
        return []


def send_prompt_to_session(session_id, text):
    """Send recording text as a prompt to a session (runs immediately)."""
    try:
        resp = requests.post(
            f"{BASE_URL}/api/session/{session_id}/prompt",
            auth=AUTH,
            json={"text": text},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            delivery = data.get("delivery", "unknown")
            return True, f"Sent to {delivery} delivery"
        return False, f"Error {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


def get_session_label(session):
    """Get a formatted label for a session."""
    import html

    import arrow

    updated = arrow.get(session["time"]["updated"] / 1000)
    diff = (arrow.now() - updated).total_seconds()
    if diff < 60:
        updated_str = "just now"
    else:
        updated_str = updated.humanize()
    agent = session.get("agent", "?")
    title = html.escape(session.get("title", "Untitled"))
    # Check if session is currently busy (running)
    busy = session.get("time", {}).get("busy")
    running_indicator = "●" if busy else "○"
    return f"{running_indicator} {updated_str}  {agent:10s}  {title}"


def get_session_project(session):
    """Get project name from session."""
    import html

    project_id = session.get("projectID", "")
    if project_id == "global":
        return html.escape("global")
    # Try to get project name from location
    location = session.get("location", {})
    directory = location.get("directory", "")
    if directory:
        return html.escape(os.path.basename(directory) or directory)
    return html.escape(project_id)


# ── UI Builder ─────────────────────────────────────────────────
def create_ui():
    """Create and return the main application window."""
    window = Gtk.Window()
    window.set_title("Opencode Prompt")
    window.set_default_size(600, 450)
    window.connect("delete-event", Gtk.main_quit)
    window.set_type_hint(Gdk.WindowTypeHint.DIALOG)

    # Main box
    main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    window.add(main_box)

    # ── Header ──
    header_box = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=10,
        hexpand=True,
        halign=Gtk.Align.START,
        valign=Gtk.Align.START,
    )
    header_box.set_margin_top(10)
    header_box.set_margin_start(10)
    header_box.set_margin_end(10)
    main_box.pack_start(header_box, False, False, 0)

    # Recording label and refresh button
    recording_box = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL, spacing=5,
        halign=Gtk.Align.START, valign=Gtk.Align.START,
    )
    recording_label = Gtk.Label()
    recording_label.set_markup('<b>Last Recording</b>')
    recording_label.set_justify(Gtk.Justification.LEFT)
    recording_box.pack_start(recording_label, False, False, 0)

    recording_refresh_button = Gtk.Button(label="⟳")
    recording_refresh_button.set_tooltip_text("Refresh recording")
    recording_refresh_button.set_halign(Gtk.Align.START)
    recording_refresh_button.set_valign(Gtk.Align.START)
    recording_box.pack_end(recording_refresh_button, False, False, 0)
    header_box.pack_start(recording_box, False, False, 0)

    # ── Recording text view ──
    recording_frame = Gtk.Frame()
    recording_frame.set_margin_top(5)
    recording_frame.set_margin_start(10)
    recording_frame.set_margin_end(10)
    main_box.pack_start(recording_frame, False, False, 0)

    recording_scrolled = Gtk.ScrolledWindow()
    recording_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    recording_scrolled.set_size_request(-1, 80)
    recording_frame.add(recording_scrolled)

    recording_textview = Gtk.TextView()
    recording_textview.set_editable(True)
    recording_textview.set_cursor_visible(True)
    recording_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    recording_textview.modify_font(Pango.FontDescription("monospace 12"))
    recording_textview.set_border_width(5)
    recording_scrolled.add(recording_textview)

    recording_buffer = recording_textview.get_buffer()

    # Load recording text once on startup
    text, filename = get_last_recording_text()
    state.last_recording_text = text
    state.last_recording_file = filename
    recording_buffer.set_text(text or "(No recordings found)")

    # ── Separator ──
    separator = Gtk.HSeparator()
    separator.set_margin_top(5)
    separator.set_margin_bottom(5)
    main_box.pack_start(separator, False, False, 0)

    # Session count and refresh row
    session_header = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL, spacing=10
    )
    session_header.set_margin_start(10)
    session_header.set_margin_end(10)
    session_header.set_margin_bottom(5)
    main_box.pack_start(session_header, False, False, 0)

    session_count_label = Gtk.Label()
    session_count_label.set_markup('<b>Active Sessions: 0</b>')
    session_count_label.set_halign(Gtk.Align.START)
    session_header.pack_start(session_count_label, False, False, 0)

    session_header.pack_end(Gtk.Label(), True, True, 0)  # spacer

    refresh_button = Gtk.Button(label="⟳")
    refresh_button.set_tooltip_text("Refresh sessions")
    refresh_button.set_halign(Gtk.Align.END)
    session_header.pack_end(refresh_button, False, False, 0)

    # ── Sessions list ──
    sessions_frame = Gtk.Frame()
    sessions_frame.set_margin_start(10)
    sessions_frame.set_margin_end(10)
    main_box.pack_start(sessions_frame, True, True, 0)

    sessions_scrolled = Gtk.ScrolledWindow()
    sessions_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sessions_frame.add(sessions_scrolled)

    sessions_listbox = Gtk.ListBox()
    sessions_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
    sessions_listbox.set_activate_on_single_click(False)
    sessions_scrolled.add(sessions_listbox)

    # Create rows for sessions
    session_rows = []

    # ── Status bar ──
    status_bar = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL, spacing=10
    )
    status_bar.set_margin_top(5)
    status_bar.set_margin_bottom(5)
    status_bar.set_margin_start(10)
    status_bar.set_margin_end(10)
    main_box.pack_end(status_bar, False, False, 0)

    status_label = Gtk.Label()
    status_label.set_markup(f'<span size="small" color="gray">{state.send_result}</span>')
    status_bar.pack_start(status_label, False, False, 0)

    help_label = Gtk.Label()
    help_label.set_markup('<span size="small" color="gray">↑/↓: navigate | Enter: send | Refresh: ⟳ button</span>')
    help_label.set_hexpand(True)
    help_label.set_halign(Gtk.Align.END)
    status_bar.pack_end(help_label, False, False, 0)

    # ── Functions ──
    def refresh_ui():
        """Refresh the UI with current session data."""
        # Get active sessions
        with state._lock:
            new_sessions = get_active_sessions()
            state.sessions = new_sessions

            # Update session count
            session_count_label.set_markup(
                f'<b>Active Sessions: {len(new_sessions)}</b>'
            )

            # Clear existing rows
            for row in session_rows[:]:
                sessions_listbox.remove(row)
                row.destroy()
            session_rows.clear()

            # Create new rows
            for i, session in enumerate(new_sessions):
                label = Gtk.Label()
                label.set_markup(get_session_label(session))
                label.set_xalign(0)
                label.set_margin_start(10)
                label.set_margin_end(10)
                label.set_margin_top(5)
                label.set_margin_bottom(5)
                label.set_selectable(True)

                project = Gtk.Label()
                project.set_markup(
                    f'<span size="small" color="gray">Project: {get_session_project(session)}</span>'
                )
                project.set_xalign(0)
                project.set_margin_start(10)
                project.set_margin_end(10)
                project.set_margin_bottom(10)

                row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                row_box.pack_start(label, False, False, 0)
                row_box.pack_start(project, False, False, 0)

                # Create a ListBoxRow subclass for event handling
                class SessionRow(Gtk.ListBoxRow):
                    def __init__(self, index):
                        super().__init__()
                        self._index = index
                        self.add(row_box)
                        self.connect("button-press-event", self.on_click)
                        self.connect("enter-notify-event", self.on_hover)
                        self.connect("leave-notify-event", self.on_leave)

                    def on_click(self, widget, event):
                        state.selected_index = self._index
                        return True

                    def on_hover(self, widget, event):
                        widget.set_state_flags(Gtk.StateFlags.PRELIGHT, True)
                        return False

                    def on_leave(self, widget, event):
                        widget.unset_state_flags(Gtk.StateFlags.PRELIGHT)
                        return False

                row = SessionRow(i)
                sessions_listbox.insert(row, -1)
                row.show_all()
                session_rows.append(row)

            # Select last index (or 0 if empty)
            if new_sessions:
                state.selected_index = min(state.selected_index, len(new_sessions) - 1)
                try:
                    sessions_listbox.select_row(session_rows[state.selected_index])
                except IndexError:
                    pass
            else:
                state.selected_index = 0

    def on_session_click(widget, event, index):
        """Handle session row click."""
        state.selected_index = index
        return True

    def on_session_hover(widget, event, index):
        """Handle session hover."""
        widget.set_state_flags(Gtk.StateFlags.PRELIGHT, True)
        return False

    def on_session_leave(widget, event, index):
        """Handle session leave."""
        widget.unset_state_flags(Gtk.StateFlags.PRELIGHT)
        return False

    def on_key_press(widget, event):
        """Handle key press events."""
        key = event.keyval

        # Up arrow - move selection up
        if key == Gdk.KEY_Up:
            with state._lock:
                if state.sessions and state.selected_index > 0:
                    state.selected_index -= 1
                    try:
                        sessions_listbox.select_row(session_rows[state.selected_index])
                    except (IndexError, AttributeError):
                        pass
            return True

        # Down arrow - move selection down
        elif key == Gdk.KEY_Down:
            with state._lock:
                if state.sessions and state.selected_index < len(state.sessions) - 1:
                    state.selected_index += 1
                    try:
                        sessions_listbox.select_row(session_rows[state.selected_index])
                    except (IndexError, AttributeError):
                        pass
            return True

        # Enter - send text to selected session
        elif key == Gdk.KEY_Return or key == Gdk.KEY_KP_Enter:
            with state._lock:
                if state.sessions and 0 <= state.selected_index < len(state.sessions):
                    session = state.sessions[state.selected_index]
                    session_id = session["id"]
                    text = state.last_recording_text

                    if not text:
                        status_label.set_markup(
                            '<span size="small" color="red">No recording text to send</span>'
                        )
                        return True

                    # Send in a background thread
                    def send_task():
                        success, message = send_prompt_to_session(session_id, text)
                        GLib.idle_add(update_status, success, message)

                    threading.Thread(target=send_task, daemon=True).start()
                    status_label.set_markup(
                        '<span size="small" color="orange">Sending...</span>'
                    )
            return True

        return False

    def update_status(success, message):
        """Update status bar with send result."""
        import html
        if success:
            with state._lock:
                if 0 <= state.selected_index < len(state.sessions):
                    title = html.escape(state.sessions[state.selected_index].get("title", "Unknown"))
                    message = f"✓ {html.escape(message)} → {title}"
            status_label.set_markup(
                f'<span size="small" color="green">{message}</span>'
            )
        else:
            status_label.set_markup(f'<span size="small" color="red">✗ {html.escape(message)}</span>')

    # ── Connections ──
    window.connect("key-press-event", on_key_press)
    refresh_button.connect("clicked", lambda btn: refresh_ui())

    # Initial load
    refresh_ui()

    return window


# ── Main ───────────────────────────────────────────────────────
def main():
    # Make sure we have a display
    if not Gdk.Display.get_default():
        print("Error: No display available. Run with a graphical environment.")
        sys.exit(1)

    window = create_ui()
    window.show_all()

    # Try to select the first row
    print("GtkApp started. Press ↑/↓ to navigate active sessions, Enter to send recording text.")
    Gtk.main()


if __name__ == "__main__":
    main()
