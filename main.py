#!/usr/bin/env python3
"""CS.Money Market Auto-Sale Tool — entry point."""

import asyncio
import signal
import sys

from src import config
from src.processor import run

config.setup_logging()


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stop_event = asyncio.Event()

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

    try:
        loop.run_until_complete(run(stop_event))
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
