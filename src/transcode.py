import gi
import sys
import time
import json
import argparse
import os
import re
import shutil
import tempfile
from pathlib import Path

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib


def parse_args():
    parser = argparse.ArgumentParser(description="Transcode MP4 using GStreamer with progress display.")
    parser.add_argument("input", type=Path, help="Path to input MP4 file")
    parser.add_argument("output", type=Path, help="Path to output MP4 file")
    parser.add_argument("--config", type=Path, default=None, help="Path to JSON config file")
    args = parser.parse_args()

    if not args.input.is_file():
        parser.error(f"Input file '{args.input}' does not exist.")

    return args


def load_config(config_path: Path | None=None):
    default = {
      "video_encoder": {
        "name": "svtav1enc",
        "properties": {
          "crf": 28,
          "parameters-string": "preset=6:enable-tf=0:enable-qm=1:qm-min=0:tune=0:enable-overlays=1:scd=1:scm=0"
        }
      },
      "audio_encoder": {
        "name": "opusenc",
        "properties": {
          "bitrate": 96000
        }
      },
      "video_caps": "video/x-raw,format=I420_10LE",
      "workdir": "/tmp/enc"
    }
    if config_path and config_path.is_file():
        with open(config_path) as f:
            return json.load(f)
    return default


def build_pipeline(input_path, output_path, config):
    # Load config values
    video_cfg = config.get("video_encoder", {})
    audio_cfg = config.get("audio_encoder", {})
    caps_str = config.get("video_caps", "video/x-raw,format=I420_10LE")

    # Validate untrusted config before instantiating elements from it.
    video_name = validate_encoder_name(
        video_cfg.get("name", "svtav1enc"), _ALLOWED_VIDEO_ENCODERS, "video")
    audio_name = validate_encoder_name(
        audio_cfg.get("name", "opusenc"), _ALLOWED_AUDIO_ENCODERS, "audio")
    video_props = validate_properties(video_cfg.get("properties", {}))
    audio_props = validate_properties(audio_cfg.get("properties", {}))

    # Create elements
    filesrc = Gst.ElementFactory.make("filesrc", "src")
    decodebin = Gst.ElementFactory.make("decodebin", "decoder")
    queue_v = Gst.ElementFactory.make("queue", "video_queue")
    videoconvert = Gst.ElementFactory.make("videoconvert", "videoconvert")
    capsfilter = Gst.ElementFactory.make("capsfilter", "capsfilter")
    encoder = Gst.ElementFactory.make(video_name, "video_encoder")
    queue_a = Gst.ElementFactory.make("queue", "audio_queue")
    audioconvert = Gst.ElementFactory.make("audioconvert", "audioconvert")
    audioresample = Gst.ElementFactory.make("audioresample", "audioresample")
    opusenc = Gst.ElementFactory.make(audio_name, "audio_encoder")
    muxer = Gst.ElementFactory.make("mp4mux", "muxer")
    sink = Gst.ElementFactory.make("filesink", "sink")

    # Validate
    elements = [filesrc, decodebin, queue_v, videoconvert, capsfilter, encoder,
                queue_a, audioconvert, audioresample, opusenc, muxer, sink]
    if not all(elements):
        raise RuntimeError("Failed to create all GStreamer elements.")

    # Set properties
    filesrc.set_property("location", str(input_path))
    sink.set_property("location", str(output_path))
    muxer.set_property("faststart", True)
    capsfilter.set_property("caps", Gst.Caps.from_string(caps_str))

    for key, value in video_props.items():
        encoder.set_property(key.replace("-", "_"), value)

    for key, value in audio_props.items():
        opusenc.set_property(key.replace("-", "_"), value)

    # Build pipeline
    pipeline = Gst.Pipeline.new("transcode_pipeline")
    for element in elements:
        pipeline.add(element)

    # Static links
    filesrc.link(decodebin)

    queue_v.link(videoconvert)
    videoconvert.link(capsfilter)
    capsfilter.link(encoder)
    encoder.link(muxer)

    queue_a.link(audioconvert)
    audioconvert.link(audioresample)
    audioresample.link(opusenc)
    opusenc.link(muxer)

    muxer.link(sink)

    def on_pad_added(decodebin, pad):
        string = pad.query_caps(None).to_string()
        if string.startswith("video/"):
            sink_pad = queue_v.get_static_pad("sink")
        elif string.startswith("audio/"):
            sink_pad = queue_a.get_static_pad("sink")
        else:
            return

        if not sink_pad.is_linked():
            pad.link(sink_pad)

    decodebin.connect("pad-added", on_pad_added)

    return pipeline


def print_progress(pipeline, start_time, duration_holder):
    if duration_holder[0] == Gst.CLOCK_TIME_NONE:
        success, dur = pipeline.query_duration(Gst.Format.TIME)
        if success:
            duration_holder[0] = dur

    success, pos = pipeline.query_position(Gst.Format.TIME)
    if not (success and duration_holder[0] and duration_holder[0] != Gst.CLOCK_TIME_NONE):
        return True

    elapsed = time.time() - start_time
    progress_ratio = pos / duration_holder[0]
    eta = (elapsed / progress_ratio - elapsed) if progress_ratio > 0 else 0

    bar_len = 50
    filled_len = int(bar_len * progress_ratio)
    bar = "#" * filled_len + "-" * (bar_len - filled_len)

    current = pos // Gst.SECOND
    total = duration_holder[0] // Gst.SECOND

    print(
        f"\rProgress: [{bar}] {progress_ratio*100:.2f}% "
        f"({current}s / {total}s) ETA: {eta:.1f}s", end="", flush=True
    )
    return True


# Input and output paths arrive as job arguments from the queue, which is an
# untrusted boundary: anyone able to enqueue a job could otherwise make the
# worker read or overwrite arbitrary files. Confine both to a single media root
# configured out-of-band (env var), never from the job args or config file.
def media_root() -> Path:
    """Return the allowlisted base directory for input/output media files."""
    root = os.environ.get("MEDIA_ROOT")
    if not root:
        raise RuntimeError(
            "MEDIA_ROOT environment variable must be set to the directory that "
            "holds the media files this worker is allowed to read and write."
        )
    return Path(root).resolve()


def resolve_media_path(path, *, must_exist: bool) -> Path:
    """Resolve a job-supplied media path and ensure it stays inside MEDIA_ROOT.

    resolve() collapses symlinks and '..', so a symlink or relative escape that
    points outside the root is rejected here rather than followed.
    """
    root = media_root()
    resolved = Path(path).resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise ValueError(
            f"Refusing media path outside the allowed root: {resolved} (root: {root})"
        )
    if must_exist and not resolved.is_file():
        raise FileNotFoundError(f"Input file does not exist: {resolved}")
    return resolved


# Encoder element names and properties also come from the (untrusted) config
# file. Restrict element instantiation to known audio/video encoders so a config
# cannot conjure arbitrary GStreamer elements (e.g. sinks with a writable
# "location" property), and constrain property keys/values to safe shapes.
_ALLOWED_VIDEO_ENCODERS = {
    "svtav1enc", "av1enc", "rav1enc", "aomenc",
    "x264enc", "x265enc",
    "vp8enc", "vp9enc",
}
_ALLOWED_AUDIO_ENCODERS = {
    "opusenc", "vorbisenc", "flacenc", "lamemp3enc",
    "avenc_aac", "fdkaacenc", "voaacenc",
}
_PROPERTY_KEY_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def validate_encoder_name(name: str, allowed: set[str], kind: str) -> str:
    if name not in allowed:
        raise ValueError(
            f"Refusing disallowed {kind} encoder '{name}'. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )
    return name


def validate_properties(properties: dict) -> dict:
    """Reject malformed property keys or non-scalar values from the config."""
    validated = {}
    for key, value in properties.items():
        if not isinstance(key, str) or not _PROPERTY_KEY_RE.match(key):
            raise ValueError(f"Refusing unsafe encoder property name: {key!r}")
        if not isinstance(value, (bool, int, float, str)):
            raise ValueError(
                f"Refusing unsafe value for property '{key}': "
                f"expected bool/int/float/str, got {type(value).__name__}"
            )
        validated[key] = value
    return validated


# Directories we refuse to use as a workdir, since cleanup deletes their
# contents recursively. The workdir comes from a (potentially untrusted)
# config, so validate it before creating or wiping anything.
_FORBIDDEN_WORKDIRS = {
    Path("/"), Path("/home"), Path("/root"), Path("/etc"), Path("/usr"),
    Path("/var"), Path("/bin"), Path("/lib"), Path("/boot"), Path("/dev"),
    Path("/proc"), Path("/sys"), Path.home(),
}


def resolve_workdir(workdir_path: Path) -> Path:
    """Resolve and validate a workdir path before it is created or wiped."""
    workdir = Path(workdir_path).resolve()
    if not workdir.is_absolute():
        raise ValueError(f"Workdir must be an absolute path: {workdir}")
    # Require some depth so we never operate on the filesystem root or a
    # top-level directory like /home or /etc.
    if len(workdir.parts) < 3 or workdir in _FORBIDDEN_WORKDIRS:
        raise ValueError(f"Refusing to use unsafe workdir: {workdir}")
    return workdir


def prepare_workdir_base(workdir_path: Path) -> Path:
    """Validate and create the base dir that holds per-run staging dirs."""
    base = resolve_workdir(workdir_path)
    # 0700 so other local users cannot read staged media. (mode is only applied
    # when we create it; an unpredictable per-run subdir is what actually
    # defeats the symlink race below regardless of the base's permissions.)
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    return base


def make_run_workdir(base: Path) -> Path:
    """Create a fresh, unpredictable 0700 staging dir for a single job.

    A fixed path like /tmp/enc/in is a symlink-race target: a local attacker
    can pre-seed it so our copies follow links to files we shouldn't touch.
    mkdtemp picks a random name and creates it with O_EXCL at mode 0700, so it
    cannot be guessed or pre-created.
    """
    run_dir = Path(tempfile.mkdtemp(prefix="enc-", dir=base))
    (run_dir / "in").mkdir(mode=0o700)
    (run_dir / "out").mkdir(mode=0o700)
    return run_dir


def cleanup_run_workdir(base: Path, run_dir: Path):
    """Remove a single job's staging dir, refusing to escape the base."""
    run_dir = run_dir.resolve()
    if run_dir == base or not run_dir.is_relative_to(base):
        raise ValueError(f"Refusing to remove staging dir outside base: {run_dir}")
    shutil.rmtree(run_dir, ignore_errors=True)


def run_transcoding(input_path: Path, output_path: Path, config_path: Path | None=None):
    # These arrive from the queue (untrusted); confine them to MEDIA_ROOT so a
    # job cannot read or overwrite files outside the allowed media directory.
    input_path = resolve_media_path(input_path, must_exist=True)
    output_path = resolve_media_path(output_path, must_exist=False)
    if not isinstance(config_path, Path) and config_path:
        config_path = Path(config_path)
    config = load_config(config_path)
    workdir_base = prepare_workdir_base(config.get("workdir", "/tmp/enc"))
    run_dir = make_run_workdir(workdir_base)
    try:
        workpath_in = run_dir / "in" / os.path.basename(input_path)
        workpath_out = run_dir / "out" / os.path.basename(output_path)
        print(f"Copying file {os.path.basename(input_path)} to {workpath_in}")
        shutil.copy(input_path, workpath_in)

        Gst.init(None)
        pipeline = build_pipeline(workpath_in, workpath_out, config)
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        loop = GLib.MainLoop()

        start_time = time.time()
        duration_holder = [Gst.CLOCK_TIME_NONE]
        succeeded = [False]

        def on_message(_, message):
            msg_type = message.type
            if msg_type == Gst.MessageType.EOS:
                print(f"\nDone! Total time: {time.time() - start_time:.2f} seconds.")
                succeeded[0] = True
                pipeline.set_state(Gst.State.NULL)
                loop.quit()
            elif msg_type == Gst.MessageType.ERROR:
                err, debug = message.parse_error()
                print(f"\nError: {err}\nDebug: {debug}")
                pipeline.set_state(Gst.State.NULL)
                loop.quit()

        bus.connect("message", on_message)

        pipeline.set_state(Gst.State.PLAYING)
        GLib.timeout_add(500, print_progress, pipeline, start_time, duration_holder)

        try:
            loop.run()
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            pipeline.set_state(Gst.State.NULL)

        if not succeeded[0]:
            raise RuntimeError("Transcoding did not complete successfully; output not written.")

        print(f"Copying processed file {os.path.basename(workpath_out)} to {output_path.absolute()}")
        shutil.copy(workpath_out, output_path)
    finally:
        print("Cleaning up")
        cleanup_run_workdir(workdir_base, run_dir)


def main():
    args = parse_args()
    print(f"Transcoding file {args.input}")
    run_transcoding(args.input, args.output, args.config)


if __name__ == "__main__":
    main()
