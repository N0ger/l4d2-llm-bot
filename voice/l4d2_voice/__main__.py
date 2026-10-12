"""Voice commands for L4D2 bots (Left 4 Bots 2 + ion's Vocalizer).

    python -m l4d2_voice                    # listen to the microphone
    python -m l4d2_voice --dry-run          # print commands instead of sending them
    python -m l4d2_voice --text             # type phrases instead of speaking
    python -m l4d2_voice --check            # which phrase words the model does not know
    python -m l4d2_voice --list-devices
"""

import argparse
import sys
from pathlib import Path

from .bridge import GameBridge, PrintBridge, find_game_dir
from .commands import CommandTable, Cooldown

COMMANDS_DIR = Path(__file__).resolve().parent.parent / "commands"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="l4d2_voice", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--lang", default="pl", help="pl or en (default: pl)")
    parser.add_argument("--commands", type=Path, help="command table (default: commands/<lang>.toml)")
    parser.add_argument("--model", type=Path, help="unpacked Vosk model directory (default: small model for --lang)")
    parser.add_argument("--game-dir", type=Path, help="the left4dead2/ folder of the game (default: auto-detect)")
    parser.add_argument("--device", help="microphone name or index (see --list-devices)")
    parser.add_argument("--min-confidence", type=float, default=0.6, help="ignore less certain recognitions (0-1)")
    parser.add_argument("--cooldown", type=float, default=1.5, help="seconds before the same command can repeat")
    parser.add_argument("--dry-run", action="store_true", help="print commands instead of sending them to the game")
    parser.add_argument("--text", action="store_true", help="read phrases from the keyboard instead of the microphone")
    parser.add_argument("--check", action="store_true", help="list phrase words missing from the model and exit")
    parser.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    return parser.parse_args(argv)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def check_vocabulary(table: CommandTable, lang: str, model: Path | None) -> int:
    from .listener import find_model_dir, model_vocabulary

    model_dir = find_model_dir(lang, model)
    vocabulary = model_vocabulary(model_dir) if model_dir else None
    if vocabulary is None:
        log("Model not found or has no graph/words.txt. Run once without --check to download it, or pass --model.")
        return 2

    missing = sorted(table.vocabulary() - vocabulary)
    if not missing:
        log(f"All {len(table.vocabulary())} words are known to {model_dir.name}.")
        return 0
    log(f"{len(missing)} words are not in {model_dir.name} and will be ignored by the recognizer:")
    for word in missing:
        log(f"  {word}")
    log("Replace them in the command table (e.g. other spellings of bot names).")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list_devices:
        from .listener import list_devices

        print(list_devices())
        return 0

    table = CommandTable.load(args.commands or COMMANDS_DIR / f"{args.lang}.toml")
    log(f"{len(table.phrases)} phrases loaded")

    if args.check:
        return check_vocabulary(table, args.lang, args.model)

    if args.dry_run:
        bridge = PrintBridge()
    else:
        game_dir = args.game_dir or find_game_dir()
        if not game_dir or not game_dir.is_dir():
            log("Left 4 Dead 2 not found. Pass --game-dir \".../steamapps/common/Left 4 Dead 2/left4dead2\".")
            return 2
        bridge = GameBridge(game_dir)
        log(f"Sending commands to {bridge.path}")

    cooldown = Cooldown(args.cooldown)

    def handle(text: str, confidence: float = 1.0) -> None:
        text = text.strip()
        action = table.match(text)
        if action is None:
            log(f"? {text}")
            return
        if confidence < args.min_confidence:
            log(f"~ {text} -> {action.label} (confidence {confidence:.2f}, ignored)")
            return
        if not cooldown.ready(action):
            return
        log(f"! {text} -> {action.label}")
        bridge.send(list(action.commands))

    try:
        if args.text:
            log("Type a phrase and press Enter (Ctrl+D to quit).")
            for line in sys.stdin:
                if line.strip():
                    handle(line)
            return 0

        from .listener import listen, load_model

        model = load_model(args.lang, args.model)
        log("Listening... (Ctrl+C to quit)")
        for utterance in listen(model, table.grammar(), args.device):
            handle(utterance.text, utterance.confidence)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
