"""Command line entry point.

    desk-assistant              run the desk on the Pi
    desk-assistant --variant haro
                                run the screenless Haro build
    desk-assistant --check      print the resolved config
    desk-assistant --test       laptop test mode
    desk-assistant --variant haro --test
                                Haro on a laptop: space toggles the mic
"""

from __future__ import annotations

import argparse
import logging
import sys

from desk_assistant.config import load_config
from desk_assistant.runtime import format_check, run_production, run_test_mode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="desk-assistant", description="Desk assistant for a Raspberry Pi.")
    parser.add_argument("--config", help="Path to config.yaml. Defaults to the usual search path.")
    parser.add_argument(
        "--variant",
        choices=("desk", "haro"),
        default="desk",
        help="desk is the touchscreen build. haro is the screenless voice build.",
    )
    parser.add_argument("--check", action="store_true", help="Print the resolved config and exit.")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Laptop test mode: keyboard keys stand in for GPIO, and a mock webhook answers.",
    )
    parser.add_argument(
        "--script",
        action="store_true",
        help="With --test, run a scripted demo against the mock server and exit.",
    )
    parser.add_argument("--screenshot-dir", default="", help="Where --script writes state screenshots.")
    parser.add_argument("--reply-delay", type=float, default=0.8, help="Mock server reply delay in seconds.")
    parser.add_argument("--windowed", action="store_true", help="Force a window instead of fullscreen.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if args.script and not args.test:
        parser.error("--script is only used with --test")

    if args.variant == "haro":
        from desk_assistant.haro_runtime import format_haro_check, run_haro_production, run_haro_test

        if args.test:
            return run_haro_test(script=args.script)
        config = load_config(args.config)
        if args.check:
            print(format_haro_check(config))
            if not config.webhook.url or not config.webhook.key:
                print("\nWebhook URL and key are not set.")
            return 0
        return run_haro_production(config)

    if args.test:
        return run_test_mode(
            reply_delay=args.reply_delay,
            windowed=True if args.windowed or args.script else True,
            script=args.script,
            screenshot_dir=args.screenshot_dir,
        )

    config = load_config(args.config)
    if args.windowed:
        config.ui.fullscreen = False
    if args.check:
        print(format_check(config))
        if not config.webhook.url or not config.webhook.key:
            print("\nWebhook URL and key are not set. The screen will explain that until they are.")
        return 0
    return run_production(config)


if __name__ == "__main__":
    sys.exit(main())
