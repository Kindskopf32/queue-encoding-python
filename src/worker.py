#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "rq",
#     "redis",
#     "PyGObject",
# ]
# ///

from redis import Redis
from rq import Worker
from transcode import run_transcoding

# Preload libraries
from work import long_running_chore
# Provide the worker with the list of queues (str) to listen to.
w = Worker(["default"], connection=Redis('$REDIS', 6379))
w.work()
