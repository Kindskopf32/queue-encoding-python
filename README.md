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

### Hardened deployment (recommended)

The worker decodes **untrusted media** with `decodebin`, which auto-plugs codecs
from `gst-libav` and `gst-plugins-{bad,ugly}`. Those codecs have a long history
of memory-safety bugs, so treat a malicious input file as potentially able to
crash or compromise the worker process. Run the container sandboxed and
resource-limited so that a codec compromise is contained and a hostile file
can't exhaust the host:

```sh
docker run -d --name encoder-worker \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --user 1000:1000 \
  --pids-limit 256 \
  --memory 4g --memory-swap 4g \
  --cpus 4 \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=8g \
  -e XDG_CACHE_HOME=/tmp/cache \
  -e REDIS_HOST=redis.example.com \
  -v /host/media:/media:rw \
  queue-encoding:alpine
```

Why each flag:

- `--read-only` — immutable root filesystem; the process can only write to the
  mounts listed below.
- `--cap-drop ALL` + `--security-opt no-new-privileges` — no Linux capabilities
  and no privilege escalation via setuid binaries.
- Keep the **default seccomp profile** (it is on unless you disable it). Do
  **not** pass `--privileged` or `--security-opt seccomp=unconfined`.
- `--user 1000:1000` — non-root (the images already default to this).
- `--pids-limit`, `--memory`/`--memory-swap` (swap disabled), `--cpus` — cap the
  blast radius of a decode bomb or runaway encode. Tune to your hardware.
- `--tmpfs /tmp` — writable staging area: the default workdir base (`/tmp/enc`)
  and the GStreamer registry cache (via `XDG_CACHE_HOME`) live here. **Size it**
  to your largest input + output, times the number of concurrent jobs, since
  each job copies both into staging. `noexec` is safe because only media/data
  files land here.
- `-v /host/media:/media:rw` — `MEDIA_ROOT`. This must stay writable because the
  worker writes the encoded output back under it; only the rootfs is read-only.

**Arch/`uv` image:** the `archlinux` image runs via `uv run` and writes a wheel
cache to `/app/.cache`, which is read-only under `--read-only`. Add an
**exec-allowing** tmpfs for it (uv loads compiled extensions from the cache, so
do not add `noexec` here):

```sh
  --tmpfs /app/.cache:rw,nosuid,nodev,size=2g
```

**Network:** the worker only needs to reach Redis and exposes no ports. Place it
on a network where nothing else is reachable, and do not publish any ports for
it.
