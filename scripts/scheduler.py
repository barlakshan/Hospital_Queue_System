"""Periodic jobs for the Docker deployment (replaces Celery worker + beat).

* every 5 minutes: re-send recently failed SMS (`flask retry-sms`)
* once a day, after local midnight: close previous days' queues
  (`flask close-day`)

The EC2 deployment runs the same two commands from cron instead
(see deploy/ec2-setup.sh).

Run: python -m scripts.scheduler
"""
import logging
import time

from app import create_app
from app.utils.timeutil import local_today

INTERVAL_SECONDS = 300

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scheduler")


def main():
    app = create_app()
    runner = app.test_cli_runner()
    closed_for = None
    while True:
        with app.app_context():
            today = local_today()
        if closed_for != today:
            result = runner.invoke(args=["close-day"])
            log.info("close-day: %s", result.output.strip() or result.exception)
            if result.exit_code == 0:
                closed_for = today
        result = runner.invoke(args=["retry-sms"])
        log.info("retry-sms: %s", result.output.strip() or result.exception)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
