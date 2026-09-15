"""
core/audio_fx.py — Procedural Sci-Fi Sound Synthesizer for IRIS.

Generates subtle, sleek auditory feedback for assistant state transitions
without requiring any external audio asset files (100% mathematical synthesis).
Non-blocking, thread-safe, and opt-in via config_manager.
"""

from __future__ import annotations

import json
import math
import sys
import threading
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
    _SD_OK = True
except ImportError:
    _SD_OK = False

_BASE_DIR    = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _BASE_DIR / "config" / "api_keys.json"
_SAMPLE_RATE = 24000


def _is_sfx_enabled() -> bool:
    try:
        if _CONFIG_PATH.exists():
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            return bool(cfg.get("sfx_enabled", True))
    except Exception:
        pass
    return True


def _set_sfx_enabled(enabled: bool) -> None:
    try:
        cfg = {}
        if _CONFIG_PATH.exists():
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        cfg["sfx_enabled"] = bool(enabled)
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    except Exception as e:
        print(f"[AudioFX] Could not save sfx setting: {e}")


def _play_pcm(samples: np.ndarray, volume: float = 0.18) -> None:
    if not _SD_OK or not _is_sfx_enabled():
        return

    def _worker():
        try:
            pcm = (samples * float(volume)).astype(np.float32)
            sd.play(pcm, samplerate=_SAMPLE_RATE, blocking=False)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def play_wake() -> None:
    """Subtle futuristic rising dual-tone chirp when IRIS wakes up."""
    dur = 0.16
    t = np.linspace(0, dur, int(_SAMPLE_RATE * dur), endpoint=False)
    # Frequency sweep 540 Hz -> 920 Hz with smooth envelope
    f = np.linspace(540, 920, len(t))
    env = np.sin(np.pi * t / dur) ** 1.5
    s1 = np.sin(2 * np.pi * f * t) * env
    s2 = np.sin(4 * np.pi * f * t) * (env * 0.35)
    _play_pcm(s1 + s2, volume=0.15)


def play_sleep() -> None:
    """Soft descending pulse when IRIS goes to sleep."""
    dur = 0.22
    t = np.linspace(0, dur, int(_SAMPLE_RATE * dur), endpoint=False)
    f = np.linspace(780, 360, len(t))
    env = (1.0 - t / dur) ** 2.0
    s1 = np.sin(2 * np.pi * f * t) * env
    _play_pcm(s1, volume=0.12)


def play_complete() -> None:
    """Crisp futuristic double-chime when an action finishes successfully."""
    dur = 0.20
    t = np.linspace(0, dur, int(_SAMPLE_RATE * dur), endpoint=False)
    env1 = np.exp(-t * 22.0)
    env2 = np.exp(-np.maximum(0, t - 0.08) * 22.0) * (t >= 0.08)
    s1 = np.sin(2 * np.pi * 659.25 * t) * env1   # E5
    s2 = np.sin(2 * np.pi * 987.77 * t) * env2   # B5
    _play_pcm(s1 + s2, volume=0.14)


def play_alert() -> None:
    """Soft warning pulse for confirmations or alerts."""
    dur = 0.18
    t = np.linspace(0, dur, int(_SAMPLE_RATE * dur), endpoint=False)
    env = np.sin(np.pi * t / dur) ** 1.8
    s1 = np.sin(2 * np.pi * 440.0 * t) * env
    s2 = np.sin(2 * np.pi * 466.16 * t) * (env * 0.5)  # slight discordance
    _play_pcm(s1 + s2, volume=0.15)
