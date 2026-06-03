FROM archlinux

COPY src/ /app/

WORKDIR /app

RUN pacman --noconfirm -Sy gst-libav gst-plugins-bad gst-plugins-base gst-plugins-good gst-plugins-ugly uv python-gobject python-redis python-cairo base-devel && mkdir -p /app/.cache/uv /media && chown -R 1000:1000 /app /media

# Directory the worker is allowed to read input from and write output to.
# Mount the host media directory here (e.g. -v /host/media:/media).
ENV MEDIA_ROOT=/media

# Run as a non-root user.
USER 1000:1000

CMD ["uv", "--cache-dir", "/app/.cache", "run", "/app/worker.py"]
