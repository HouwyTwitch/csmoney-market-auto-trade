#!/usr/bin/env python3
"""CS.Money Market Auto-Sale Tool — entry point."""

import asyncio
import signal

from src import config
from src.processor import run

config.setup_logging()


def main():
    stop_event = asyncio.Event()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        loop.run_until_complete(run(stop_event))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
