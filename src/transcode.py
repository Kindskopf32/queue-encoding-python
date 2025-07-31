import gi
import sys
import time
import json
import argparse
import os
import shutil
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

    # Create elements
    filesrc = Gst.ElementFactory.make("filesrc", "src")
    decodebin = Gst.ElementFactory.make("decodebin", "decoder")
    queue_v = Gst.ElementFactory.make("queue", "video_queue")
    videoconvert = Gst.ElementFactory.make("videoconvert", "videoconvert")
    capsfilter = Gst.ElementFactory.make("capsfilter", "capsfilter")
    encoder = Gst.ElementFactory.make(video_cfg.get("name", "svtav1enc"), "video_encoder")
    queue_a = Gst.ElementFactory.make("queue", "audio_queue")
    audioconvert = Gst.ElementFactory.make("audioconvert", "audioconvert")
    audioresample = Gst.ElementFactory.make("audioresample", "audioresample")
    opusenc = Gst.ElementFactory.make(audio_cfg.get("name", "opusenc"), "audio_encoder")
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

    for key, value in video_cfg.get("properties", {}).items():
        encoder.set_property(key.replace("-", "_"), value)

    for key, value in audio_cfg.get("properties", {}).items():
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


def prepare_workdir(workdir_path: Path):
    workdir = workdir_path
    workdirs = (workdir, os.path.join(workdir, "in"), os.path.join(workdir, "out"))
    for dir in workdirs:
        dir = Path(dir)
        if dir and not dir.is_dir():
            print(f"Creating dir {dir}")
            dir.mkdir()
        elif dir and dir.is_dir():
            print(f"Dir {dir} exists")
        else:
            raise OSError(f"Could not create workdir {dir}")


def cleanup_workdir(workdir_path: Path):
    workdir = workdir_path
    for root, dirs, files in workdir.walk():
        for name in files:
            print(f"Deleting {root / name}")
            (root / name).unlink()


def run_transcoding(input_path: Path, output_path: Path, config_path: Path | None=None):
    if not isinstance(input_path, Path):
        input_path = Path(input_path)
    if not isinstance(output_path, Path):
        output_path = Path(output_path)
    if not isinstance(config_path, Path) and config_path:
        config_path = Path(config_path)
    config = load_config(config_path)
    workdir_path = config.get("workdir", "/tmp/enc")
    prepare_workdir(workdir_path)
    workpath_in = Path(os.path.join(workdir_path, "in", os.path.basename(input_path)))
    workpath_out = Path(os.path.join(workdir_path, "out", os.path.basename(output_path)))
    print(f"Copying file {os.path.basename(input_path)} to {workpath_in}")
    shutil.copy(input_path, workpath_in)

    Gst.init(None)
    pipeline = build_pipeline(workpath_in, workpath_out, config)
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    loop = GLib.MainLoop()

    start_time = time.time()
    duration_holder = [Gst.CLOCK_TIME_NONE]

    def on_message(_, message):
        msg_type = message.type
        if msg_type == Gst.MessageType.EOS:
            print(f"\nDone! Total time: {time.time() - start_time:.2f} seconds.")
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

    print(f"Copying processed file {os.path.basename(workpath_out)} to {output_path.absolute()}")
    shutil.copy(workpath_out, output_path)

    print("Cleaning up")
    cleanup_workdir(Path(workdir_path))


def main():
    args = parse_args()
    print(f"Transcoding file {args.input}")
    run_transcoding(args.input, args.output, args.config)


if __name__ == "__main__":
    main()
