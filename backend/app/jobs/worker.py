"""Separate worker process for larger deployments: python -m app.jobs.worker"""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from app.jobs.scheduler import build

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build(BlockingScheduler).start()
