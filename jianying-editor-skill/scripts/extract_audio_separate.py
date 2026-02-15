import argparse
import os
import subprocess
import sys


def _require_file(path: str) -> str:
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise SystemExit(f"ERROR: file not found: {path}")
    return path


def _run(cmd: list[str]) -> None:
    # Keep stdout/stderr attached so ffmpeg progress/errors are visible.
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a JianYing draft that imports a video and a separately extracted audio track."
    )
    parser.add_argument("--video", required=True, help="Absolute path to the input video (e.g. .mp4).")
    parser.add_argument(
        "--project",
        default=None,
        help="Draft name to create/overwrite. Default: AudioExtract_<video_basename>.",
    )
    parser.add_argument(
        "--drafts-root",
        default=None,
        help="Override JianYing drafts root (normally auto-detected via LOCALAPPDATA).",
    )
    parser.add_argument(
        "--audio-bitrate",
        default="192k",
        help="MP3 bitrate used when extracting audio (default: 192k).",
    )
    parser.add_argument(
        "--mute-video",
        action="store_true",
        help="Set the imported video clip volume to 0 to avoid double-audio.",
    )
    parser.add_argument(
        "--video-track",
        default="VideoTrack",
        help="Timeline track name for the video clip (default: VideoTrack).",
    )
    parser.add_argument(
        "--audio-track",
        default="ExtractedAudio",
        help="Timeline track name for the extracted audio clip (default: ExtractedAudio).",
    )
    args = parser.parse_args()

    video_path = _require_file(args.video)
    base = os.path.splitext(os.path.basename(video_path))[0]
    project_name = args.project or f"AudioExtract_{base}"

    # Import JyProject from sibling script dir.
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from jy_wrapper import JyProject  # noqa: E402

    project = JyProject(project_name, drafts_root=args.drafts_root, overwrite=True)

    # Keep extracted audio inside the draft folder, so the project is self-contained.
    temp_assets_dir = os.path.join(project.root, project.name, "temp_assets")
    os.makedirs(temp_assets_dir, exist_ok=True)
    audio_out = os.path.join(temp_assets_dir, "extracted_audio.mp3")

    print(f"🎬 Video: {video_path}")
    print(f"🎵 Audio: {audio_out}")

    # Extract audio (re-encode to MP3 for broad compatibility).
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            video_path,
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            str(args.audio_bitrate),
            audio_out,
        ]
    )

    video_seg = project.add_media_safe(video_path, start_time="0s", track_name=args.video_track)
    if video_seg and args.mute_video:
        video_seg.volume = 0.0

    project.add_audio_safe(audio_out, start_time="0s", track_name=args.audio_track)
    project.save()

    print(f"✅ Draft created: {project_name}")
    print(f"📂 Draft root: {project.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

