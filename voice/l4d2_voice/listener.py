"""Microphone -> Vosk (offline, grammar-restricted) -> recognized text."""

import json
import queue
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# Small models support runtime grammars; the big ones do not.
DEFAULT_MODELS = {
    "pl": "vosk-model-small-pl-0.22",
    "en": "vosk-model-small-en-us-0.15",
}


@dataclass
class Utterance:
    text: str
    confidence: float  # mean per-word confidence, 0..1


def load_model(lang: str, model_path: Path | None):
    import vosk

    vosk.SetLogLevel(-1)
    if model_path:
        return vosk.Model(str(model_path))
    # Downloads the small model into ~/.cache/vosk on first use.
    return vosk.Model(model_name=DEFAULT_MODELS.get(lang), lang=lang)


def find_model_dir(lang: str, model_path: Path | None) -> Path | None:
    if model_path:
        return model_path
    import vosk

    name = DEFAULT_MODELS.get(lang)
    for base in filter(None, vosk.MODEL_DIRS):
        if name and (Path(base) / name).is_dir():
            return Path(base) / name
    return None


def model_vocabulary(model_dir: Path) -> set[str] | None:
    words_file = model_dir / "graph" / "words.txt"
    if not words_file.is_file():
        return None
    with open(words_file, encoding="utf-8") as f:
        return {line.split()[0] for line in f if line.strip()}


def list_devices() -> str:
    import sounddevice as sd

    return str(sd.query_devices())


def listen(model, grammar: list[str], device: str | int | None = None) -> Iterator[Utterance]:
    """Yield one Utterance per spoken phrase (after a short pause)."""
    import sounddevice as sd
    import vosk

    if isinstance(device, str) and device.isdigit():
        device = int(device)
    samplerate = int(sd.query_devices(device, "input")["default_samplerate"])

    recognizer = vosk.KaldiRecognizer(model, samplerate, json.dumps(grammar, ensure_ascii=False))
    recognizer.SetWords(True)

    blocks: queue.Queue[bytes] = queue.Queue()

    def on_audio(indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        blocks.put(bytes(indata))

    with sd.RawInputStream(
        samplerate=samplerate, blocksize=samplerate // 10, device=device, dtype="int16", channels=1, callback=on_audio
    ):
        while True:
            if not recognizer.AcceptWaveform(blocks.get()):
                continue
            result = json.loads(recognizer.Result())
            text = result.get("text", "").strip()
            words = [w for w in result.get("result", []) if w.get("word") != "[unk]"]
            if not text or not words:
                continue
            confidence = sum(w.get("conf", 0.0) for w in words) / len(words)
            yield Utterance(text, confidence)
