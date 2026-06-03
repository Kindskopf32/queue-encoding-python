#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "rq==2.9.0",
#     "redis==8.0.0",
#     "PyGObject==3.56.3",
# ]
# ///

import os

from redis import Redis
from rq import Worker
from transcode import run_transcoding

# Preload the job functions so RQ can resolve them when running jobs.
from work import long_running_chore
# Provide the worker with the list of queues (str) to listen to.
# Redis host is read from the REDIS_HOST env var so it is never hardcoded.
w = Worker(["default"], connection=Redis(os.environ.get("REDIS_HOST", "localhost"), 6379))
w.work()
