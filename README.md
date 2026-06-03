# queue-encoding-python
Using the jq python library and gstreamer queue videos to encode and also do it in parallel with multiple workers

### Necessary changes
Set the `REDIS_HOST` environment variable to the address of your redis instance
(defaults to `localhost`). For example: `REDIS_HOST=redis.example.com`.

Set the `MEDIA_ROOT` environment variable to the directory that holds the media
files the worker is allowed to read input from and write output to. Job-supplied
input/output paths are confined to this directory, so a job cannot make the
worker read or overwrite files elsewhere on the host. There is no default: the
worker refuses to run unless `MEDIA_ROOT` is set. The Docker images default it to
`/media`; mount your media directory there (e.g. `-v /host/media:/media`).
