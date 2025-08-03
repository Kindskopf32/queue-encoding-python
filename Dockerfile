FROM archlinux

COPY src/ /app/

WORKDIR /app

RUN pacman --noconfirm -Sy gst-libav gst-plugins-bad gst-plugins-base gst-plugins-good gst-plugins-ugly uv python-gobject python-redis python-cairo base-devel && mkdir -p /app/.cache/uv && chown -R 1000:1000 /app/.cache

CMD ["uv", "--cache-dir", "/app/.cache", "run", "/app/worker.py"]
