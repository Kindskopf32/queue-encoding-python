#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "rq==2.9.0",
#     "redis==8.0.0",
#     "PyGObject==3.56.3",
# ]
# ///

import os
import argparse
import re

import redis
import rq

from transcode import run_transcoding

parser = argparse.ArgumentParser()
parser.add_argument("--item", help="item to add to queue for processing", required=True)
parser.add_argument("--config", help="Path to the config file to override some settings", default=None)
args = parser.parse_args()
q = rq.Queue(connection=redis.Redis(os.environ.get("REDIS_HOST", "localhost"), 6379))

infile = args.item
outfile = os.path.join(os.path.dirname(args.item), re.sub(r'\.mp4$', '.av1.mp4', os.path.basename(args.item)))
print(f"Would encode file {infile} to {outfile}")
if args.config:
    job = q.enqueue(run_transcoding, infile, outfile, args.config, job_timeout=43200)
else:
    job = q.enqueue(run_transcoding, infile, outfile, job_timeout=43200)
