# queue-encoding-python
Using the jq python library and gstreamer queue videos to encode and also do it in parallel with multiple workers

### Necessary changes
Set the `REDIS_HOST` environment variable to the address of your redis instance
(defaults to `localhost`). For example: `REDIS_HOST=redis.example.com`.
