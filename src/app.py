#!/usr/bin/env python3
"""
Mic Recorder - Background audio recorder activated by global hotkey.

Saves 16kHz/16-bit/mono WAV files (Vosk-compatible) to ~/recordings/

Usage:
    python app.py              # Run in foreground
    python app.py &            # Run in background

Hotkey: Ctrl+T  (toggle recording on/off)
Right-click tray icon to quit.
"""

import os
import sys
import threading
import wave
from datetime import datetime

import pyaudio
import pystray
from PIL import Image, ImageDraw
from pynput import keyboard

# ── Configuration ──────────────────────────────────────────────
RECORD_DIR = os.path.expanduser("~/recordings")
MODEL_PATH = os.path.expanduser("~/.cache/vosk/vosk-model-small-en-us-0.15")
SAMPLE_FORMAT = pyaudio.paInt16
CHANNELS = 1
SAMPLE_RATE = 16000    # Vosk standard
CHUNK = 1024


# ── State ──────────────────────────────────────────────────────
is_recording = False
_stop_event = None
_indicator_win = None
_vosk_model = None


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
    global _indicator_win
    try:
        from gi.repository import Gtk, Gdk

        # Hide any existing overlay
        hide_overlay()

        _indicator_win = Gtk.Window.new(Gtk.WindowType.POPUP)
        _indicator_win.set_app_paintable(True)

        screen = Gdk.Screen.get_default()
        width = Gdk.Screen.width(screen)
        _indicator_win.set_size_request(120, 32)
        _indicator_win.move(width - 140, 8)
        _indicator_win.set_opacity(0.8)

        # Custom draw for rounded rect with red dot
        def on_draw(widget, cr):
            width, height = widget.get_size()
            cr.set_source_rgba(0.15, 0.15, 0.18, 0.85)
            cr.round_rect(0, 0, width, height, 8)
            cr.fill()

            # Red dot
            cr.set_source_rgba(220 / 255, 54 / 255, 54 / 255, 1)
            cr.arc(24, height // 2, 7, 0, 2 * 3.14159)
            cr.fill()

            # White "R" text
            cr.set_source_rgba(1, 1, 1, 1)
            cr.select_font_face("sans", Gdk.FontSlant.NORMAL, Gdk.FontWeight.BOLD)
            cr.set_font_size(16)
            cr.move_to(38, height // 2 + 6)
            cr.show_text("REC")

        _indicator_win.connect("draw", on_draw)
        _indicator_win.show_all()
        _indicator_win.raise_()
        return _indicator_win
    except (ImportError, AttributeError):
        return None


def hide_overlay():
    global _indicator_win
    if _indicator_win is not None:
        _indicator_win.hide()
        _indicator_win.destroy()
        _indicator_win = None


# ── Audio recording ───────────────────────────────────────────
def _record(stop_event):
    """Record audio until stop_event is set."""
    pa = None
    try:
        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=SAMPLE_FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK,
        )

        frames = []
        while not stop_event.is_set():
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)

        stream.stop_stream()
        stream.close()

        # Write WAV directly (16kHz, 16-bit, mono — Vosk compatible)
        os.makedirs(RECORD_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dest = os.path.join(RECORD_DIR, f"recording_{ts}.wav")

        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(RECORD_DIR, f"recording_{ts}_{counter}.wav")
            counter += 1

        with wave.open(dest, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(pa.get_sample_size(SAMPLE_FORMAT))
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b''.join(frames))

        print(f"  Saved: {dest}", file=sys.stderr)

        # Transcribe with Vosk
        text = _transcribe(dest)
        if text is not None:
            txt_dest = dest.replace(".wav", ".txt")
            with open(txt_dest, "w") as f:
                f.write(text)
            print(f"  Text:  {txt_dest}", file=sys.stderr)

    finally:
        if pa:
            pa.terminate()


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


# ── Hotkey handling ───────────────────────────────────────────
_pressed_keys = set()
_hotkey_lock = threading.Lock()


def _on_press(key):
    global is_recording, _stop_event

    with _hotkey_lock:
        # Track pressed keys
        try:
            _pressed_keys.add(key)
        except TypeError:
            pass

        # Check for Ctrl+T
        ctrl = any(
            isinstance(k, keyboard.Key) and k in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r)
            for k in _pressed_keys
        )
        t = any(
            hasattr(k, 'char') and k.char == 't'
            for k in _pressed_keys
        )

        if ctrl and t:
            if is_recording:
                # Stop
                is_recording = False
                _stop_event.set()
                hide_overlay()
            else:
                # Start
                is_recording = True
                _stop_event = threading.Event()
                t = threading.Thread(target=_record, args=(_stop_event,), daemon=True)
                t.start()
                show_overlay()

            # Update tray icon
            _update_tray()


def _on_release(key):
    with _hotkey_lock:
        try:
            _pressed_keys.discard(key)
        except TypeError:
            pass


# ── Tray icon ──────────────────────────────────────────────────
_tray = None


def _update_tray():
    global _tray
    if _tray is None:
        return

    status = "Recording... Ctrl+T to stop" if is_recording else "Idle — Ctrl+T to record"

    _tray.icon = pystray.Icon(
        "mic-recorder",
        image=make_icon(is_recording),
        title=status,
        menu=pystray.Menu(
            pystray.MenuItem(status, lambda i: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda i: _stop()),
        ),
    )


def _stop(icon):
    icon.stop()


def create_tray():
    global _tray
    _tray = pystray.Icon(
        "mic-recorder",
        image=make_icon(False),
        title="Idle — Ctrl+T to record",
        menu=pystray.Menu(
            pystray.MenuItem("Idle — Ctrl+T to record", lambda i: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda i: _stop()),
        ),
    )
    return _tray


# ── Main ───────────────────────────────────────────────────────
def main():
    global _vosk_model

    os.makedirs(RECORD_DIR, exist_ok=True)

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

    # Start hotkey listener (works on X11 without root)
    with keyboard.Listener(on_press=_on_press, on_release=_on_release) as listener:
        print(f"\ud83c\udf99\ufe0f  Mic Recorder started.")
        print(f"    Recordings \u2192 {RECORD_DIR}")
        print(f"    Press Ctrl+T to start/stop recording + transcription")
        print(f"    Right-click tray icon to quit.\n")

        # Run tray in the main thread
        tray.run()


if __name__ == "__main__":
    main()
