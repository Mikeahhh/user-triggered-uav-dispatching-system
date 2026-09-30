#!/usr/bin/env python3


from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path


RECORDER_VERSION = "1.2.0"
EVENT_PREFIX = "RECORDER_EVENT "


def emit(event: str, **fields) -> None:
    body = {"event": event, "recorder_version": RECORDER_VERSION}
    body.update(fields)
    print(EVENT_PREFIX + json.dumps(body, sort_keys=True), flush=True)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device_index", type=int)
    parser.add_argument("output_path")
    parser.add_argument("--max-read-failures", type=int, default=100)
    parser.add_argument("--version", action="version", version=RECORDER_VERSION)
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    os.environ.pop("DISPLAY", None)
    try:
        import cv2
    except ImportError:
        emit("ERROR", error_code="OPENCV_IMPORT_FAILED")
        return 8

    final_path = Path(args.output_path).expanduser().resolve()
    temp_path = final_path.with_name(final_path.stem + ".part" + final_path.suffix)
    if final_path.exists() or temp_path.exists():
        emit("ERROR", error_code="OUTPUT_EXISTS")
        return 2
    final_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    stopped = threading.Event()
    paused = threading.Event()

    def request_stop(_signum=None, _frame=None):
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    cap = cv2.VideoCapture(args.device_index, cv2.CAP_V4L2)
    if not cap.isOpened():
        emit("ERROR", error_code="CAMERA_OPEN_FAILED")
        return 3

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if width <= 0 or height <= 0:
        cap.release()
        emit("ERROR", error_code="INVALID_FRAME_SIZE")
        return 4
    if not 1.0 <= fps <= 240.0:
        fps = 30.0

    writer = cv2.VideoWriter(
        str(temp_path),
        cv2.VideoWriter_fourcc(*"XVID"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        cap.release()
        emit("ERROR", error_code="VIDEO_WRITER_OPEN_FAILED")
        return 5
    try:
        os.chmod(temp_path, 0o600)
    except OSError:
        pass

    emit(
        "READY",
        width=width,
        height=height,
        fps=round(fps, 3),
        opencv_version=cv2.__version__,
        output_file=final_path.name,
    )

    def stdin_reader():
        try:
            for line in sys.stdin:
                command = line.strip().lower()
                if command == "pause" and not paused.is_set():
                    paused.set()
                    emit("PAUSED")
                elif command == "resume" and paused.is_set():
                    paused.clear()
                    emit("RESUMED")
                elif command == "stop":
                    emit("STOPPING")
                    stopped.set()
                    return

            emit("STOPPING", reason="PARENT_STDIN_CLOSED")
            stopped.set()
        except Exception:
            stopped.set()

    threading.Thread(target=stdin_reader, daemon=True).start()

    frames_written = 0
    consecutive_failures = 0
    frame_interval = 1.0 / fps
    error_code = ""
    try:
        while not stopped.is_set():
            loop_started = time.monotonic()
            ok, frame = cap.read()
            if not ok:
                consecutive_failures += 1
                if consecutive_failures >= max(1, args.max_read_failures):
                    error_code = "CAMERA_READ_FAILED"
                    break
                stopped.wait(0.02)
                continue
            consecutive_failures = 0
            if not paused.is_set():
                writer.write(frame)
                frames_written += 1
            elapsed = time.monotonic() - loop_started
            if elapsed < frame_interval:
                stopped.wait(frame_interval - elapsed)
    except Exception as exc:
        error_code = "CAPTURE_EXCEPTION_{}".format(type(exc).__name__)
    finally:
        writer.release()
        cap.release()

    if error_code:


        retained_file = ""
        if frames_written > 0 and temp_path.exists():
            incomplete_path = final_path.with_name(final_path.stem + ".incomplete" + final_path.suffix)
            try:
                if incomplete_path.exists():
                    retained_file = temp_path.name
                else:
                    os.replace(temp_path, incomplete_path)
                    retained_file = incomplete_path.name
            except OSError:
                retained_file = temp_path.name
        else:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        emit("ERROR", error_code=error_code, frames_written=frames_written,
             incomplete_file=retained_file)
        return 6
    if frames_written <= 0:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        emit("ERROR", error_code="NO_FRAMES_WRITTEN", frames_written=0)
        return 7

    try:
        fd = os.open(temp_path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        emit("ERROR", error_code="FINALIZE_SYNC_FAILED", frames_written=frames_written,
             incomplete_file=temp_path.name)
        return 9
    try:
        os.replace(temp_path, final_path)
    except OSError:
        emit("ERROR", error_code="FINALIZE_FAILED", frames_written=frames_written,
             incomplete_file=temp_path.name)
        return 9
    emit("DONE", output_file=final_path.name, frames_written=frames_written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
