#!/usr/bin/env python3
"""
Mic Recorder - Background audio recorder activated by global hotkey.

Saves 16kHz/16-bit/mono WAV files (Vosk-compatible) to ~/recordings/

Usage:
    python app.py              # Run in foreground
    python app.py &            # Run in background

Hotkey: Ctrl+Shift+R  (toggle recording on/off)
Right-click tray icon to quit.
"""

import os
os.environ["ALSA_PCM_NO_ASYNC_SIGNALS"] = "1"  # suppress ALSA warnings
import re
import signal as _signal
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime

gi = None  # lazy import

import pyaudio
import pystray
import pyperclip
from PIL import Image, ImageDraw
from pynput import keyboard

# ── Config ─────────────────────────────────────────────────────
from config_window import (
    get_post_transcription_action,
    get_post_transcription_command,
    show_config_window,
    _config_window_ref,
)

# ── Configuration ──────────────────────────────────────────────
RECORD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
MODEL_PATH = os.path.expanduser("~/.cache/vosk/vosk-model-small-en-us-0.15")
SAMPLE_FORMAT = pyaudio.paInt16
CHANNELS = 1
SAMPLE_RATE = 16000    # Vosk standard
CHUNK = 1024
INPUT_DEVICE = 1       # Razer Seiren X (hw:2,0) — see src/app.py constants for options


# ── State ──────────────────────────────────────────────────────
is_recording = False
_stop_event = None
_indicator_win = None
_vosk_model = None
_pid_file = os.path.expanduser("~/.local/share/mic-recorder/pid")


# ── Notifications ─────────────────────────────────────────────
_tray_icon_ref = None


def _notify(title, body, icon="audio-input-microphone"):
    """Send a desktop notification."""
    try:
        subprocess.run(
            ["notify-send", "-i", icon, "--hint", "string:x-canonical-private-synchronous:microphone",
             title, body],
            capture_output=True, timeout=5
        )
    except Exception:
        print(f"  [notify] {title}: {body}", file=sys.stderr)


def _basename(path):
    """Get filename without extension and path."""
    name = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r"^recording_\d{4}-\d{2}-\d{2}_\d{6}_?", "", name).replace("_", " ") or "recording"


# ── Icon generation ───────────────────────────────────────────
def make_icon(recording=False):
    """Create a 64x64 icon image."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle
    draw.ellipse([4, 4, 60, 60], fill=(45, 45, 48, 255))

    if recording:
        # Red recording indicator
        draw.ellipse([14, 14, 50, 50], fill=(220, 54, 54, 255))
        draw.ellipse([22, 22, 42, 42], fill=(255, 255, 255, 255))
    else:
        # Microphone icon in white
        draw.pieslice([18, 12, 46, 38], 0, 180, fill=(200, 200, 200, 255))
        draw.rectangle([28, 28, 36, 48], fill=(200, 200, 200, 255))
        draw.arc([28, 48, 36, 56], 0, 180, fill=(200, 200, 200, 255))
        # Stand
        draw.arc([16, 24, 48, 56], 180, 360, fill=(200, 200, 200, 255), width=4)

    return img


# ── Recording indicator overlay ───────────────────────────────
def show_overlay():
    """Show a small recording indicator overlay in the upper-right corner."""
    try:
        from gi.repository import Gtk, Gdk, GLib

        def _show():
            global _indicator_win
            _do_hide_overlay()

            _indicator_win = Gtk.Window.new(Gtk.WindowType.POPUP)
            _indicator_win.set_app_paintable(True)

            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor()
            geometry = monitor.get_geometry()
            screen_width = geometry.width

            _indicator_win.set_size_request(120, 32)
            _indicator_win.move(screen_width - 140, 8)
            _indicator_win.set_keep_above(True)

            def on_draw(widget, cr):
                global gi
                if gi is None:
                    import gi as _gi
                    gi = _gi
                    gi.require_version("PangoCairo", "1.0")
                from gi.repository import Pango, PangoCairo
                w, h = widget.get_size()
                radius = 8
                x, y = 0, 0
                cr.set_source_rgba(0.15, 0.15, 0.18, 0.85)
                cr.move_to(x + radius, y)
                cr.line_to(x + w - radius, y)
                cr.arc(x + w - radius, y + radius, radius, -0.5 * 3.14159, 0)
                cr.line_to(x + w, y + h - radius)
                cr.arc(x + w - radius, y + h - radius, radius, 0, 0.5 * 3.14159)
                cr.line_to(x + radius, y + h)
                cr.arc(x + radius, y + h - radius, radius, 0.5 * 3.14159, 1.0 * 3.14159)
                cr.line_to(x, y + radius)
                cr.arc(x + radius, y + radius, radius, 1.0 * 3.14159, 1.5 * 3.14159)
                cr.close_path()
                cr.fill()

                cr.set_source_rgba(220 / 255, 54 / 255, 54 / 255, 1)
                cr.arc(24, h // 2, 7, 0, 2 * 3.14159)
                cr.fill()

                cr.set_source_rgba(1, 1, 1, 1)
                layout = PangoCairo.create_layout(cr)
                desc = Pango.FontDescription("sans bold 16")
                layout.set_font_description(desc)
                layout.set_text("REC")
                cr.move_to(38, h // 2 + 4)
                PangoCairo.update_layout(cr, layout)

            _indicator_win.connect("draw", on_draw)
            _indicator_win.show()

        GLib.idle_add(_show)
        return True
    except (ImportError, AttributeError):
        return None


def hide_overlay():
    try:
        from gi.repository import GLib

        GLib.idle_add(_do_hide_overlay)
        return True
    except (ImportError, AttributeError):
        return None


def _do_hide_overlay():
    global _indicator_win
    if _indicator_win is not None:
        _indicator_win.hide()
        _indicator_win.destroy()
        _indicator_win = None


# ── Audio recording ───────────────────────────────────────────
_recording_frames = []
_frames_lock = threading.Lock()
_stop_recording = threading.Event()
_recording_ts = None  # timestamp set at START time


def _record():
    """Record audio until _stop_recording is set."""
    pa = None
    stream = None
    global _recording_ts
    try:
        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=SAMPLE_FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK,
            input_device_index=INPUT_DEVICE,
        )
        stream.start_stream()

        global _recording_frames
        _recording_frames = []
        start_ts = _recording_ts  # capture at start
        print(f"  [REC] T{threading.get_ident()} start_ts={start_ts}", file=sys.stderr)
        while not _stop_recording.is_set():
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                with _frames_lock:
                    _recording_frames.append(data)
            except OSError:
                break
        print(f"  [REC] loop exited, {len(_recording_frames)} frames", file=sys.stderr)

        stream.stop_stream()
        stream.close()

        frames = None
        with _frames_lock:
            frames = _recording_frames
            _recording_frames = []

        # Write WAV (16kHz, 16-bit, mono — Vosk compatible)
        os.makedirs(RECORD_DIR, exist_ok=True)
        write_ts = _recording_ts  # capture at write time
        print(f"  [REC] T{threading.get_ident()} write_ts={write_ts}, start_ts={start_ts}", file=sys.stderr)
        dest = os.path.join(RECORD_DIR, f"recording_{write_ts}.wav")

        counter = 1
        print(f"  [REC] checking dest={dest}, exists={os.path.exists(dest)}", file=sys.stderr)
        while os.path.exists(dest):
            dest = os.path.join(RECORD_DIR, f"recording_{_recording_ts}_{counter}.wav")
            counter += 1
            print(f"  [REC] exists, trying counter={counter}", file=sys.stderr)
        print(f"  [REC] final dest={dest}", file=sys.stderr)

        print(f"  [REC] WRITING {dest}", file=sys.stderr)
        with wave.open(dest, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(pa.get_sample_size(SAMPLE_FORMAT))
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b''.join(frames))

        print(f"  Saved: {dest}", file=sys.stderr)
        _notify("Recording saved", os.path.basename(dest))

        # Start transcription in background thread
        threading.Thread(target=_transcribe_and_save, args=(dest,), daemon=True).start()

    finally:
        if pa:
            pa.terminate()


def _close_current_stream():
    """Signal the recording thread to stop."""
    _stop_recording.set()


def _reset_recording_state():
    """Reset recording state for a new recording session."""
    global _stop_recording, _recording_ts
    _stop_recording = threading.Event()
    _recording_ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    print(f"  [REC] session started, ts={_recording_ts}", file=sys.stderr)


def _transcribe_and_save(wav_path):
    """Transcribe a WAV file in background, save .txt, apply configured action."""
    _notify("Transcribing", os.path.basename(wav_path))

    text = _transcribe(wav_path)
    if text is not None:
        txt_dest = wav_path.replace(".wav", ".txt")
        with open(txt_dest, "w") as f:
            f.write(text)
        print(f"  Text:  {txt_dest}", file=sys.stderr)

        # Apply the configured post-transcription action
        action = get_post_transcription_action()
        if action == "cmd":
            _run_command(text)
        else:
            # default: copy to clipboard
            _copy_text_to_clipboard(text)

        # Truncate text for notification (max 100 chars)
        preview = text[:100] + ("..." if len(text) > 100 else "")
        _notify("Transcription complete", preview)
    else:
        _notify("Transcription failed", "Could not transcribe audio")


def _run_command(text):
    """Run the configured command with $TEXT replaced by the transcribed text."""
    cmd_template = get_post_transcription_command()
    cmd = cmd_template.replace("$TEXT", text)
    try:
        subprocess.run(
            ["/bin/sh", "-c", cmd],
            check=False,
            timeout=30,
        )
        print(f"  [command] ran: {cmd}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"  [command] timeout: {cmd}", file=sys.stderr)
    except Exception as exc:
        print(f"  [command] error: {exc}", file=sys.stderr)


def _copy_text_to_clipboard(text):
    """Copy text to the system clipboard via pyperclip."""
    try:
        pyperclip.copy(text)
        print(f"  [clipboard] copied", file=sys.stderr)
    except Exception:
        print(f"  [clipboard] warning: clipboard copy failed", file=sys.stderr)


def _transcribe(wav_path):
    """Transcribe a WAV file using Vosk. Returns text or None on failure."""
    try:
        import json
        from vosk import KaldiRecognizer, SetLogLevel

        if _vosk_model is None:
            return None

        SetLogLevel(-1)
        wf = wave.open(wav_path, "rb")
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            wf.close()
            return None

        rec = KaldiRecognizer(_vosk_model, wf.getframerate())
        result = ""

        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                r = json.loads(rec.Result())
                result = r.get("text", "")

        final = json.loads(rec.FinalResult())
        result = final.get("text", result)
        wf.close()
        return result.strip()
    except Exception:
        return None


_hotkey_lock = threading.Lock()
_recording_thread = None

# ── Tray icon ──────────────────────────────────────────────────
_tray = None


def _toggle_recording():
    """Toggle recording state. Called by sxhkd signal or tray icon."""
    global is_recording, _stop_event, _hotkey_active, _recording_thread

    with _hotkey_lock:
        if is_recording:
            print(f"  [KEY] STOP (signal)", file=sys.stderr)
            is_recording = False
            _close_current_stream()
            if _recording_thread and _recording_thread.is_alive():
                for _ in range(30):
                    if not _recording_thread.is_alive():
                        break
                    time.sleep(0.1)
                _recording_thread.join(timeout=0.5)
                print(f"  [KEY] thread joined", file=sys.stderr)
            hide_overlay()
            print(f"  [KEY] STOP done", file=sys.stderr)
        else:
            print(f"  [KEY] START (signal)", file=sys.stderr)
            is_recording = True
            _reset_recording_state()
            _recording_thread = threading.Thread(target=_record, daemon=True)
            print(f"  [REC] NEW THREAD {_recording_thread.name} STARTED", file=sys.stderr)
            _recording_thread.start()
            show_overlay()
            print(f"  [KEY] START done", file=sys.stderr)
        _update_tray()


def _update_tray():
    """Update tray icon. Currently a no-op — visual feedback comes from overlay + notifications."""
    pass


def _stop(icon):
    # Close config window if open
    if _config_window_ref is not None:
        _config_window_ref.destroy()
        _config_window_ref = None
    icon.stop()


def _settings(_icon):
    show_config_window()


def create_tray():
    global _tray
    _tray = pystray.Icon(
        "mic-recorder",
        icon=make_icon(False),
        title="Idle — Ctrl+Shift+R to record",
        menu=pystray.Menu(
            pystray.MenuItem("Idle — Ctrl+Shift+R to record", lambda i: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings", _settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda i: _stop(i)),
        ),
    )
    return _tray


# ── Main ───────────────────────────────────────────────────────
def main():
    global _vosk_model

    os.makedirs(RECORD_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(_pid_file), exist_ok=True)

    # Write PID file for sxhkd to find us
    with open(_pid_file, "w") as f:
        f.write(str(os.getpid()))

    # Handle SIGUSR1 from sxhkd (toggle recording)
    def _signal_handler(signum, frame):
        threading.Thread(target=_toggle_recording, daemon=True).start()

    _signal.signal(_signal.SIGUSR1, _signal_handler)

    # Load Vosk model once at startup
    try:
        from vosk import Model
        _vosk_model = Model(MODEL_PATH)
        print(f"  Model loaded: {MODEL_PATH}")
    except Exception:
        print(f"  Warning: Vosk model not found at {MODEL_PATH}")
        print(f"  Recordings will be saved but not transcribed.")
        print()

    tray = create_tray()

    print(f"Mic Recorder started.")
    print(f"    Recordings: {RECORD_DIR}")
    print(f"    Hotkey: Ctrl+Shift+R (via sxhkd)")
    print(f"    Right-click tray icon to quit.\n")
    time.sleep(0.5)

    # Run tray in the main thread
    tray.run()


if __name__ == "__main__":
    main()
