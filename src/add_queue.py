#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "rq",
#     "redis",
#     "PyGObject",
# ]
# ///

import time
import random
import json
import os
from pathlib import PurePath
import argparse
import re

import redis
import rq

from work import long_running_chore, exec_script # this is the long running job
from transcode import run_transcoding

parser = argparse.ArgumentParser()
parser.add_argument("--item", help="item to add to queue for processing", required=True)
parser.add_argument("--config", help="Path to the config file to override some settings", default=None)
args = parser.parse_args()
q = rq.Queue(connection=redis.Redis('$REDIS', 6379))

#while True:
    # Here we insert the long running function (job) along with any parameters
#    result = q.enqueue(long_running_chore, random.randint(10, 20))
#    time.sleep(2)

#for i in range(5):
#    job = q.enqueue(exec_script, f"file_{i}")
#job = q.enqueue(exec_script, args.item, job_timeout=43200)
#print(args.item)
#print(os.path.dirname(args.item))
#print(os.path.basename(args.item))
#print(f"{os.path.splitext(os.path.basename(args.item))[0]}.av1.mp4")
infile = os.path.join(os.path.dirname(args.item), os.path.basename(args.item))
outfile = os.path.join(os.path.dirname(args.item), f"{re.sub(r'.mp4$', r'.av1.mp4', os.path.basename(args.item))}")
print(f"Would encode file {infile} to {outfile}")
if args.config:
    job = q.enqueue(run_transcoding, infile, outfile, args.config, job_timeout=43200)
else:
    job = q.enqueue(run_transcoding, infile, outfile, job_timeout=43200)
