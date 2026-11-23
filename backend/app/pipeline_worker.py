"""Separate durable pipeline loop; Agent jobs retain their own state machine."""
import logging
import time
from .pipeline_execution import tick
from .pipeline import schedule_tick


def main():
    logging.basicConfig(level=logging.INFO)
    last_schedule = 0
    while True:
        try:
            if time.monotonic()-last_schedule >= 15:
                schedule_tick(); last_schedule = time.monotonic()
            if not tick(): time.sleep(.5)
        except Exception:
            logging.exception("Pipeline loop failed")
            time.sleep(2)


if __name__ == "__main__": main()
