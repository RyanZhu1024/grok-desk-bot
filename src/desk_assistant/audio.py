"""Recording, speech-to-text, and text-to-speech.

Each stage is a small interface so Vosk, whisper.cpp, Piper, and espeak-ng
can be swapped from the config file. `auto` uses whichever offline engine is
actually installed.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Protocol

from desk_assistant.config import Config

logger = logging.getLogger(__name__)


class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> Path: ...


class SpeechToText(Protocol):
    def transcribe(self, wav_path: Path) -> str: ...


class TextToSpeech(Protocol):
    def speak(self, text: str) -> None: ...
    def stop(self) -> None: ...


class MockRecorder:
    """Writes a short silent wav. Used when there is no microphone."""

    def __init__(self, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> Path:
        self.started = False
        return write_silence_wav(0.2, self.sample_rate)


class ArecordRecorder:
    """Records with arecord, which is the reliable path on Raspberry Pi OS."""

    def __init__(self, sample_rate: int = 16000, device: str = "") -> None:
        self.sample_rate = sample_rate
        self.device = device
        self._proc: subprocess.Popen | None = None
        self._path: str | None = None

    def start(self) -> None:
        if shutil.which("arecord") is None:
            raise RuntimeError("arecord is not installed. Install the alsa-utils package.")
        handle, name = tempfile.mkstemp(suffix=".wav", prefix="desk-")
        os.close(handle)
        command = [
            "arecord",
            "-q",
            "-f",
            "S16_LE",
            "-r",
            str(self.sample_rate),
            "-c",
            "1",
            "-t",
            "wav",
        ]
        if self.device:
            command.extend(["-D", self.device])
        command.append(name)
        self._proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self._path = name

    def stop(self) -> Path:
        proc = self._proc
        path = self._path
        if proc is None or path is None:
            raise RuntimeError("Not recording.")
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        self._proc = None
        error = b""
        if proc.stderr is not None:
            error = proc.stderr.read() or b""
        wav = Path(path)
        if not wav.exists() or wav.stat().st_size < 44:
            detail = error.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "The microphone did not produce any audio.")
        return wav


class MockSTT:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.calls: list[str] = []

    def transcribe(self, wav_path: Path) -> str:
        self.calls.append(str(wav_path))
        return self.transcript


class UnavailableSTT:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def transcribe(self, wav_path: Path) -> str:
        raise RuntimeError(self.reason)


class VoskSTT:
    def __init__(self, model_path: str) -> None:
        if not model_path or not Path(model_path).is_dir():
            raise RuntimeError(
                f"Vosk model not found at {model_path or '(empty)'}. "
                "Run install.sh --with-vosk, or set stt.vosk_model."
            )
        import vosk

        vosk.SetLogLevel(-1)
        self._model = vosk.Model(model_path)

    def transcribe(self, wav_path: Path) -> str:
        import json

        import vosk

        with wave.open(str(wav_path), "rb") as wav_file:
            if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
                raise RuntimeError("Vosk expects a mono 16-bit PCM wav.")
            recognizer = vosk.KaldiRecognizer(self._model, wav_file.getframerate())
            while True:
                data = wav_file.readframes(4000)
                if not data:
                    break
                recognizer.AcceptWaveform(data)
            result = json.loads(recognizer.FinalResult())
        return str(result.get("text") or "").strip()


class WhisperCppSTT:
    """Shells out to whisper.cpp's whisper-cli. The binary and model are config."""

    def __init__(self, binary: str, model: str) -> None:
        if shutil.which(binary) is None and not Path(binary).is_file():
            raise RuntimeError(f"whisper.cpp binary not found: {binary}")
        if not model or not Path(model).is_file():
            raise RuntimeError(f"whisper.cpp model not found: {model or '(empty)'}")
        self.binary = binary
        self.model = model

    def transcribe(self, wav_path: Path) -> str:
        outbase = f"{wav_path}.whisper"
        command = [
            self.binary,
            "-m",
            self.model,
            "-f",
            str(wav_path),
            "-otxt",
            "-of",
            outbase,
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"whisper.cpp failed: {detail}") from exc
        transcript = Path(outbase + ".txt")
        if not transcript.is_file():
            alt = Path(str(wav_path) + ".txt")
            transcript = alt if alt.is_file() else transcript
        if not transcript.is_file():
            raise RuntimeError("whisper.cpp did not write a transcript.")
        try:
            return transcript.read_text(encoding="utf-8").strip()
        finally:
            transcript.unlink(missing_ok=True)


class MockTTS:
    def __init__(self) -> None:
        self.spoken: list[str] = []
        self._stopped = False

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def stop(self) -> None:
        self._stopped = True


class UnavailableTTS:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def speak(self, text: str) -> None:
        raise RuntimeError(self.reason)

    def stop(self) -> None:
        return None


class _SubprocessTTS:
    """Synthesize a wav, then play it with a process we can terminate."""

    def __init__(self, player: str, output_device: str = "") -> None:
        self.player = player
        self.output_device = output_device
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def speak(self, text: str) -> None:
        handle, name = tempfile.mkstemp(suffix=".wav", prefix="desk-tts-")
        os.close(handle)
        try:
            self.synthesize(text, name)
            command = player_argv(self.player, name, self.output_device)
            with self._lock:
                self._proc = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc = self._proc
            proc.wait()
        finally:
            with self._lock:
                self._proc = None
            try:
                os.unlink(name)
            except OSError:
                pass

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def synthesize(self, text: str, wav_path: str) -> None:
        raise NotImplementedError


class EspeakTTS(_SubprocessTTS):
    def __init__(self, binary: str, voice: str, speed: int, player: str, output_device: str = "") -> None:
        super().__init__(player, output_device)
        if shutil.which(binary) is None:
            raise RuntimeError(f"{binary} is not installed.")
        self.binary = binary
        self.voice = voice
        self.speed = str(speed)

    def synthesize(self, text: str, wav_path: str) -> None:
        subprocess.run(
            [self.binary, "-w", wav_path, "-v", self.voice, "-s", self.speed, text],
            check=True,
            capture_output=True,
        )


class PiperTTS(_SubprocessTTS):
    def __init__(self, binary: str, model: str, player: str, output_device: str = "") -> None:
        super().__init__(player, output_device)
        if shutil.which(binary) is None and not Path(binary).is_file():
            raise RuntimeError(f"Piper binary not found: {binary}")
        if not model or not Path(model).is_file():
            raise RuntimeError(f"Piper voice model not found: {model or '(empty)'}")
        self.binary = binary
        self.model = model

    def synthesize(self, text: str, wav_path: str) -> None:
        try:
            subprocess.run(
                [self.binary, "--model", self.model, "--output_file", wav_path],
                input=text.encode("utf-8"),
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Piper failed: {detail}") from exc


def write_silence_wav(seconds: float, sample_rate: int = 16000) -> Path:
    handle, name = tempfile.mkstemp(suffix=".wav", prefix="desk-silence-")
    os.close(handle)
    frames = max(1, int(seconds * sample_rate))
    with wave.open(name, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frames)
    return Path(name)


def detect_player() -> str | None:
    for name in ("paplay", "aplay", "ffplay", "afplay"):
        if shutil.which(name):
            return name
    return None


def player_argv(player: str, wav: str, device: str = "") -> list[str]:
    if player == "ffplay":
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", wav]
    if player == "afplay":
        return ["afplay", wav]
    if player == "paplay":
        command = ["paplay"]
        if device:
            command.extend(["--device", device])
        command.append(wav)
        return command
    if player == "aplay":
        command = ["aplay", "-q"]
        if device:
            command.extend(["-D", device])
        command.append(wav)
        return command
    return [player, wav]


def build_recorder(config: Config, *, force_mock: bool = False) -> tuple[Recorder, str]:
    if force_mock:
        return MockRecorder(config.audio.sample_rate), "mock recorder"
    if shutil.which("arecord"):
        return (
            ArecordRecorder(config.audio.sample_rate, config.audio.device),
            "arecord",
        )
    return MockRecorder(config.audio.sample_rate), "mock recorder (arecord not installed)"


def build_stt(config: Config, *, prefer_mock: bool = False) -> tuple[SpeechToText, str]:
    engine = config.stt.engine
    if prefer_mock and engine in {"auto", "mock"}:
        return MockSTT(config.stt.mock_transcript), "mock speech-to-text"
    try:
        if engine == "mock":
            return MockSTT(config.stt.mock_transcript), "mock speech-to-text"
        if engine == "vosk" or (
            engine == "auto" and config.stt.vosk_model and Path(config.stt.vosk_model).is_dir()
        ):
            return VoskSTT(config.stt.vosk_model), f"vosk ({config.stt.vosk_model})"
        whisper_ready = config.stt.whisper_model and Path(config.stt.whisper_model).is_file() and (
            shutil.which(config.stt.whisper_bin) or Path(config.stt.whisper_bin).is_file()
        )
        if engine == "whisper_cpp" or (engine == "auto" and whisper_ready):
            return (
                WhisperCppSTT(config.stt.whisper_bin, config.stt.whisper_model),
                f"whisper.cpp ({config.stt.whisper_model})",
            )
        if engine == "auto":
            raise RuntimeError(
                "No speech-to-text model is installed. "
                "Run install.sh --with-vosk, or set stt.engine and a model path."
            )
        raise RuntimeError(f"Unknown speech-to-text engine: {engine}")
    except Exception as exc:
        if prefer_mock:
            logger.warning("Speech-to-text unavailable (%s); using the test transcript", exc)
            return MockSTT(config.stt.mock_transcript), "mock speech-to-text"
        return UnavailableSTT(str(exc)), f"unavailable ({exc})"


def build_tts(config: Config, *, prefer_mock: bool = False) -> tuple[TextToSpeech, str]:
    if prefer_mock or config.tts.engine == "mock":
        return MockTTS(), "mock text-to-speech"
    player = config.tts.player
    if player == "auto":
        detected = detect_player()
        if not detected:
            reason = "No audio player found. Install pulseaudio-utils (paplay) or alsa-utils (aplay)."
            if prefer_mock:
                return MockTTS(), "mock text-to-speech"
            return UnavailableTTS(reason), f"unavailable ({reason})"
        player = detected
    output_device = config.audio.output_device.strip()
    device_note = f", {output_device}" if output_device else ""
    try:
        engine = config.tts.engine
        piper_ready = bool(config.tts.piper_model) and Path(config.tts.piper_model).is_file() and (
            shutil.which(config.tts.piper_bin) or Path(config.tts.piper_bin).is_file()
        )
        if engine == "piper" or (engine == "auto" and piper_ready):
            return (
                PiperTTS(config.tts.piper_bin, config.tts.piper_model, player, output_device),
                f"piper ({player}{device_note})",
            )
        espeak_bin = config.tts.espeak_bin
        if shutil.which(espeak_bin) is None and shutil.which("espeak"):
            espeak_bin = "espeak"
        if engine in {"espeak", "auto"} and shutil.which(espeak_bin):
            return (
                EspeakTTS(espeak_bin, config.tts.espeak_voice, config.tts.espeak_speed, player, output_device),
                f"{espeak_bin} ({player}{device_note})",
            )
        if engine == "auto":
            raise RuntimeError("Neither Piper nor espeak-ng is installed.")
        raise RuntimeError(f"Unknown text-to-speech engine: {engine}")
    except Exception as exc:
        logger.warning("Text-to-speech unavailable: %s", exc)
        return UnavailableTTS(str(exc)), f"unavailable ({exc})"
