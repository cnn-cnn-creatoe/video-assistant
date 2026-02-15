from __future__ import annotations

import os
import subprocess


def ffprobe_duration_seconds(media_path: str) -> float:
    """
    Return media duration in seconds using ffprobe. Returns 0.0 on failure.
    """
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                os.path.abspath(media_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        if r.returncode != 0:
            return 0.0
        return float((r.stdout or "").strip() or "0")
    except Exception:
        return 0.0


def make_storyboard_image(
    video_path: str,
    out_image_path: str,
    *,
    tiles_x: int = 4,
    tiles_y: int = 4,
    scale_width: int = 320,
) -> str:
    """
    Create a contact-sheet storyboard image (default 4x4) for a video using ffmpeg.
    Returns out_image_path.
    """
    video_path = os.path.abspath(video_path)
    out_image_path = os.path.abspath(out_image_path)
    os.makedirs(os.path.dirname(out_image_path), exist_ok=True)

    frames = tiles_x * tiles_y
    dur = ffprobe_duration_seconds(video_path) or 60.0
    # Sample N frames across the whole duration: total frames ~= duration * (N/duration) = N.
    fps_expr = f"{frames}/{dur:.6f}"

    vf = f"fps={fps_expr},scale={scale_width}:-1,tile={tiles_x}x{tiles_y}"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            video_path,
            "-vf",
            vf,
            "-frames:v",
            "1",
            out_image_path,
        ],
        check=True,
        timeout=300,
    )
    return out_image_path

