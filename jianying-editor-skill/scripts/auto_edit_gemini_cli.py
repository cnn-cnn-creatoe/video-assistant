from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Optional

from gemini_cli_bridge import GeminiCliError, run_gemini_cli
from media_storyboard import make_storyboard_image


# Force UTF-8 output for Windows consoles (emoji-safe).
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}

DEFAULT_GEMINI_TEXT_MODEL = "gemini-3-pro-preview"
DEFAULT_GEMINI_VISION_MODEL = "gemini-3-pro-preview"

ALLOWED_TRANSITIONS = ["叠化", "闪白", "向左", "向右", "向上", "向下", "缩放", "模糊"]
DEFAULT_TRANSITION = "叠化"


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


def _ensure_dir(p: str) -> str:
    os.makedirs(p, exist_ok=True)
    return p


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _extract_json_from_text(text: str) -> Any:
    """
    Extract the first top-level JSON value from a model response.

    Gemini often wraps JSON in Markdown fences and the JSON object may contain inner arrays.
    Naive slicing like `text[text.find('['):text.rfind(']')]` can accidentally grab an inner array
    (e.g. the value of `"tags": [...]`) instead of the whole object. We therefore scan and match
    braces/brackets while respecting JSON strings.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")

    # Remove markdown fences if any. Prefer fenced blocks (odd indices), then fall back to the longest chunk.
    if "```" in text:
        chunks = text.split("```")
        fenced = [chunks[i].strip() for i in range(1, len(chunks), 2) if chunks[i].strip()]
        candidates = fenced if fenced else [c.strip() for c in chunks if c.strip()]
        # Prefer chunks that look like they contain JSON.
        candidates.sort(key=lambda s: (("{" in s) or ("[" in s), len(s)), reverse=True)
        text = candidates[0] if candidates else ""

        # Strip optional language hint like "json\n".
        low = text.lstrip().lower()
        if low.startswith("json"):
            text = text.lstrip()[4:].strip()

    def _find_span(s: str) -> tuple[int, int]:
        start = -1
        for i, ch in enumerate(s):
            if ch in "{[":
                start = i
                break
        if start == -1:
            raise ValueError("no json start bracket found")

        stack: list[str] = []
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                stack.append("}")
                continue
            if ch == "[":
                stack.append("]")
                continue
            if ch in "}]":
                if not stack:
                    continue
                if ch != stack[-1]:
                    continue
                stack.pop()
                if not stack:
                    return start, i

        raise ValueError("unterminated json value")

    # Try extracting a balanced JSON substring first.
    try:
        s, e = _find_span(text)
        return json.loads(text[s : e + 1])
    except Exception:
        # Fall back to parsing the full text.
        return json.loads(text)


def _sanitize_transition(name: Any, allowed: list[str]) -> str:
    n = str(name).strip() if name is not None else ""
    if n in allowed:
        return n
    # Common garbage from LLMs like "??" should never reach pyJianYingDraft.
    return DEFAULT_TRANSITION


def _normalize_model_arg(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = str(s).strip()
    if (not s) or (s.lower() in {"auto", "default", "none", "null"}):
        return None
    return s


def _sanitize_matches(
    matches: Any,
    *,
    allowed_transitions: list[str],
    max_material_id: int,
    allow_reuse: bool,
) -> list[dict[str, Any]]:
    if not isinstance(matches, list):
        raise ValueError("matches must be a list")

    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for m in matches:
        if not isinstance(m, dict):
            continue
        try:
            srt_idx = int(m.get("srt_idx"))
        except Exception:
            continue

        mid_raw = m.get("id", None)
        mid: Optional[int]
        if mid_raw is None:
            mid = None
        else:
            try:
                mid = int(mid_raw)
            except Exception:
                mid = None

        if mid is not None and (mid < 0 or mid > max_material_id):
            mid = None

        if (not allow_reuse) and (mid is not None):
            if mid in used:
                mid = None
            else:
                used.add(mid)

        tname = _sanitize_transition(m.get("transition"), allowed_transitions)
        out.append({"srt_idx": srt_idx, "id": mid, "transition": tname})

    return out


def format_srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000.0))
    s = ms // 1000
    ms = ms % 1000
    m = s // 60
    s = s % 60
    h = m // 60
    m = m % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


@dataclass
class SrtItem:
    idx: int
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def parse_srt_content(content: str) -> list[SrtItem]:
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = [l.strip() for l in content.split("\n")]

    items: list[SrtItem] = []
    i = 0
    while i < len(lines):
        if lines[i].isdigit() and i + 1 < len(lines) and "-->" in lines[i + 1]:
            idx = int(lines[i])
            time_line = lines[i + 1]
            txt_lines: list[str] = []
            j = i + 2
            while j < len(lines) and lines[j] and not lines[j].isdigit():
                txt_lines.append(lines[j])
                j += 1

            start_str, end_str = [p.strip() for p in time_line.split("-->")[:2]]

            def _parse_time(t: str) -> float:
                t = t.replace(",", ".")
                parts = t.split(":")
                if len(parts) != 3:
                    return 0.0
                h, m, s = parts
                return float(h) * 3600.0 + float(m) * 60.0 + float(s)

            start = _parse_time(start_str)
            end = _parse_time(end_str)
            text = " ".join([t for t in txt_lines if t]).strip()
            items.append(SrtItem(idx=idx, start=start, end=end, text=text))
            i = j
            continue
        i += 1
    return items


def transcribe_to_srt_faster_whisper(
    media_path: str,
    out_srt_path: str,
    *,
    model_size: str = "small",
    language: Optional[str] = None,
) -> str:
    """
    Local transcription using faster-whisper. This avoids Gemini CLI audio limitations.
    """
    from faster_whisper import WhisperModel

    media_path = os.path.abspath(media_path)
    out_srt_path = os.path.abspath(out_srt_path)
    os.makedirs(os.path.dirname(out_srt_path), exist_ok=True)

    # Conservative defaults for CPU.
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def _has_non_ascii(p: str) -> bool:
        try:
            p.encode("ascii")
            return False
        except Exception:
            return True

    try:
        segments, _info = model.transcribe(
            media_path,
            language=language,
            vad_filter=True,
        )
    except Exception as e:
        # On some Windows builds, PyAV can't open Unicode paths. Work around by extracting
        # a mono 16k WAV to an ASCII-safe cache path and transcribing that instead.
        if sys.platform == "win32" and (_has_non_ascii(media_path) or "Invalid argument" in str(e)):
            tmp_wav = os.path.join(
                os.path.dirname(out_srt_path),
                f"fw_audio_{_sha1(media_path)}.wav",
            )
            if not os.path.exists(tmp_wav):
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        media_path,
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-c:a",
                        "pcm_s16le",
                        tmp_wav,
                    ],
                    check=True,
                )
            segments, _info = model.transcribe(
                tmp_wav,
                language=language,
                vad_filter=True,
            )
        else:
            raise

    blocks: list[str] = []
    idx = 1
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        blocks.append(str(idx))
        blocks.append(f"{format_srt_timestamp(seg.start)} --> {format_srt_timestamp(seg.end)}")
        blocks.append(text)
        blocks.append("")
        idx += 1

    _write_text(out_srt_path, "\n".join(blocks).strip() + "\n")
    return out_srt_path


@dataclass
class MaterialInfo:
    id: int
    path: str
    kind: str  # video|image
    duration: float
    desc: str
    tags: list[str]


def collect_material_files(inputs: list[str], *, limit: int = 0) -> list[str]:
    files: list[str] = []
    for inp in inputs:
        p = os.path.abspath(inp)
        if os.path.isfile(p):
            files.append(p)
            continue
        if os.path.isdir(p):
            for root, _dirs, fs in os.walk(p):
                for name in fs:
                    ext = os.path.splitext(name)[1].lower()
                    if ext in VIDEO_EXTS or ext in IMAGE_EXTS:
                        files.append(os.path.join(root, name))
    # Stable order
    files = sorted(set(files))
    if limit and limit > 0:
        return files[:limit]
    return files


def _copy_into_cache(src_path: str, cache_media_dir: str) -> str:
    src_path = os.path.abspath(src_path)
    ext = os.path.splitext(src_path)[1].lower()
    key = _sha1(src_path + str(os.path.getmtime(src_path)))
    dst = os.path.join(cache_media_dir, f"{key}{ext}")
    if not os.path.exists(dst):
        _ensure_dir(cache_media_dir)
        shutil.copy2(src_path, dst)
    return dst


def analyze_materials_with_gemini(
    material_paths: list[str],
    *,
    cache_dir: str,
    vision_model: Optional[str] = None,
    force: bool = False,
) -> list[MaterialInfo]:
    """
    For each material file, generate a cached visual proxy inside workspace:
    - video -> storyboard image (contact sheet)
    - image -> copy into cache (so Gemini can read it in-workspace)
    Then ask Gemini CLI to output JSON: {tags: [...], desc: "..."}.
    """
    cache_dir = os.path.abspath(cache_dir)
    cache_media_dir = _ensure_dir(os.path.join(cache_dir, "media"))
    cache_story_dir = _ensure_dir(os.path.join(cache_dir, "storyboards"))
    cache_json_path = os.path.join(cache_dir, "material_analysis.json")

    cache: dict[str, Any] = {}
    if os.path.exists(cache_json_path):
        try:
            cache = json.loads(_read_text(cache_json_path))
        except Exception:
            cache = {}

    results: list[MaterialInfo] = []
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def _rel(p: str) -> str:
        try:
            return os.path.relpath(p, workspace_root)
        except Exception:
            return p

    for p in material_paths:
        abs_p = os.path.abspath(p)
        ext = os.path.splitext(abs_p)[1].lower()
        kind = "video" if ext in VIDEO_EXTS else "image"
        mtime = 0.0
        try:
            mtime = os.path.getmtime(abs_p)
        except Exception:
            pass

        cached = cache.get(abs_p)
        if (not force) and cached and float(cached.get("mtime", 0)) == float(mtime):
            results.append(
                MaterialInfo(
                    id=len(results),
                    path=abs_p,
                    kind=cached.get("kind", kind),
                    duration=float(cached.get("duration", 0.0)),
                    desc=str(cached.get("desc", "")),
                    tags=list(cached.get("tags", [])),
                )
            )
            continue

        # Build a Gemini-readable image inside workspace.
        if kind == "video":
            # Storyboard output is stable per file+mtime.
            key = _sha1(abs_p + str(mtime))
            storyboard_path = os.path.join(cache_story_dir, f"{key}.jpg")
            if not os.path.exists(storyboard_path):
                make_storyboard_image(abs_p, storyboard_path)
            vision_image = storyboard_path
        else:
            vision_image = _copy_into_cache(abs_p, cache_media_dir)

        # Quick duration (video only). For images, duration is 0; timeline duration comes from SRT.
        duration = 0.0
        if kind == "video":
            # Use ffprobe via storyboard module (already called inside make_storyboard).
            # To avoid extra probe calls, estimate duration from cache if present; else leave 0.
            duration = float(cached.get("duration", 0.0)) if cached else 0.0

        stdin_prompt = (
            "你是专业短视频剪辑助手。\n"
            f"请读取图片文件：{_rel(vision_image)}\n\n"
            "任务：用中文总结画面内容。\n"
            "输出要求：只输出一个 JSON 对象（不要 Markdown，不要代码块，不要任何解释文字），字段如下：\n"
            "- tags: 字符串数组，3-8 个中文短标签（不要包含文件名）\n"
            "- desc: 字符串，1-2 句中文描述\n"
            "标签尽量用通用语义词（人物/动作/场景/物体）。\n"
        )

        try:
            def _call(model: Optional[str]):
                return run_gemini_cli(
                    "读取STDIN并按要求只输出JSON。",
                    model=model,
                    stdin_text=stdin_prompt,
                    include_directories=[workspace_root, cache_dir],
                    cwd=workspace_root,
                    timeout_s=600,
                )

            g = _call(vision_model)
            # If user forced a text-only model, auto-fallback to CLI default for vision.
            if vision_model and any(
                s in (g.response or "")
                for s in [
                    "无法直接分析图片",
                    "cannot directly analyze",
                    "only return raw data",
                    "只能返回图片的原始数据",
                ]
            ):
                g = _call(None)

            try:
                analysis = _extract_json_from_text(g.response)
            except Exception:
                # Vision models are usually selected automatically by the CLI when model is unset.
                if vision_model:
                    g = _call(None)
                    analysis = _extract_json_from_text(g.response)
                else:
                    raise
            tags = analysis.get("tags") if isinstance(analysis, dict) else None
            desc = analysis.get("desc") if isinstance(analysis, dict) else None
            if not isinstance(tags, list):
                tags = []
            tags = [str(t).strip() for t in tags if str(t).strip()]
            if not isinstance(desc, str):
                desc = ""

            def _looks_like_file_metadata(_tags: list[str], _desc: str) -> bool:
                if not _desc:
                    return False
                meta_markers = [".gemini_cache", "缓存", "目录", "文件名", "哈希", "media"]
                tag_markers = {"png", "jpg", "jpeg", "webp", "image", "file", "media", "cache", "文件", "图片"}
                return any(m in _desc for m in meta_markers) and any(
                    (str(t).strip().lower() in tag_markers) for t in _tags
                )

            if vision_model and ((not tags and not desc) or _looks_like_file_metadata(tags, desc)):
                # Helpful debug + retry: some forced models won't do vision reliably.
                try:
                    raw_path = os.path.join(cache_dir, "analysis_raw_last.txt")
                    _write_text(raw_path, g.response or "")
                except Exception:
                    pass
                g = _call(None)
                analysis = _extract_json_from_text(g.response)
                tags = analysis.get("tags") if isinstance(analysis, dict) else None
                desc = analysis.get("desc") if isinstance(analysis, dict) else None
                if not isinstance(tags, list):
                    tags = []
                tags = [str(t).strip() for t in tags if str(t).strip()]
                if not isinstance(desc, str):
                    desc = ""
        except GeminiCliError as e:
            tags, desc = [], ""
            print(f"[warn] Gemini analyze failed for: {abs_p}\n  {e}")
        except Exception as e:
            tags, desc = [], ""
            print(f"[warn] Parse failed for: {abs_p}\n  {e}")

        cache[abs_p] = {
            "mtime": mtime,
            "kind": kind,
            "duration": duration,
            "tags": tags,
            "desc": desc,
            "vision_image": vision_image,
        }
        _write_text(cache_json_path, json.dumps(cache, ensure_ascii=False, indent=2))

        results.append(
            MaterialInfo(
                id=len(results),
                path=abs_p,
                kind=kind,
                duration=duration,
                desc=desc,
                tags=tags,
            )
        )

    return results


def gemini_match_srt_to_materials(
    srt_items: list[SrtItem],
    materials: list[MaterialInfo],
    *,
    cache_dir: str,
    text_model: Optional[str] = None,
    style_hint: str = "",
    force: bool = False,
    allow_reuse: bool = False,
) -> list[dict[str, Any]]:
    cache_dir = os.path.abspath(cache_dir)
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    cache_json_path = os.path.join(cache_dir, "srt_matches.json")
    if (not force) and os.path.exists(cache_json_path):
        try:
            return json.loads(_read_text(cache_json_path))
        except Exception:
            pass

    subs = [
        {
            "idx": it.idx,
            "start": round(it.start, 3),
            "end": round(it.end, 3),
            "duration": round(it.duration, 3),
            "text": it.text,
        }
        for it in srt_items
        if it.text
    ]
    mats = [
        {
            "id": m.id,
            "file": os.path.basename(m.path),
            "kind": m.kind,
            "dur": round(float(m.duration or 0.0), 3),
            "tags": m.tags,
            "desc": m.desc,
        }
        for m in materials
    ]

    transitions = ALLOWED_TRANSITIONS

    stdin_prompt = (
        "你是专业短视频剪辑师，请把字幕段落匹配到最合适的素材。\n"
        + (f"风格提示：{style_hint}\n" if style_hint else "")
        + "素材列表（JSON）：\n"
        + json.dumps(mats, ensure_ascii=False)
        + "\n\n字幕段（JSON）：\n"
        + json.dumps(subs, ensure_ascii=False)
        + "\n\n输出严格JSON数组（不要Markdown，不要解释）。输出要求：\n"
        + "- 数组长度必须等于字幕段数量（上面字幕段 JSON 的元素个数），不得省略任何段。\n"
        + "- 数组顺序必须与字幕段顺序一致。\n"
        + "- 每个字幕段恰好输出一条记录：{\"srt_idx\": <字幕idx>, \"id\": <素材id或null>, \"transition\": <转场名>}。\n"
        + "- 如果没有合适素材，id 设为 null。\n"
        + ("- 素材可以重复使用。\n" if allow_reuse else "- 每个素材 id 最多使用一次；如果素材不足，优先保证关键字幕段，其余段 id= null。\n")
        + "- transition 必须从以下列表选择："
        + json.dumps(transitions, ensure_ascii=False)
        + "\n"
    )

    g = run_gemini_cli(
        "按STDIN中的规则输出JSON数组（只输出JSON，不要Markdown）。",
        model=text_model,
        stdin_text=stdin_prompt,
        include_directories=[workspace_root, cache_dir],
        cwd=workspace_root,
        timeout_s=600,
    )

    try:
        matches = _extract_json_from_text(g.response)
    except Exception as e:
        # Persist the raw response for debugging.
        raw_path = os.path.join(cache_dir, "srt_matches_raw.txt")
        try:
            _write_text(raw_path, g.response or "")
        except Exception:
            pass
        raise RuntimeError(f"Failed to parse Gemini matches JSON: {e}. Raw saved: {raw_path}") from e
    if not isinstance(matches, list):
        raise RuntimeError("Gemini returned non-list matches JSON.")

    matches = _sanitize_matches(
        matches,
        allowed_transitions=transitions,
        max_material_id=len(materials) - 1,
        allow_reuse=allow_reuse,
    )

    # If Gemini can't (or won't) pick any id at all, fall back to a simple deterministic assignment
    # so users still get a playable rough cut.
    if matches and materials and all(m.get("id") is None for m in matches):
        if allow_reuse:
            for i, m in enumerate(matches):
                m["id"] = i % len(materials)
                m["transition"] = _sanitize_transition(m.get("transition"), transitions)
        else:
            for i, m in enumerate(matches):
                m["id"] = i if i < len(materials) else None
                m["transition"] = _sanitize_transition(m.get("transition"), transitions)

    _write_text(cache_json_path, json.dumps(matches, ensure_ascii=False, indent=2))
    return matches


def main() -> int:
    ap = argparse.ArgumentParser(description="Gemini CLI powered auto editor -> JianYing draft")
    ap.add_argument("--project", required=True, help="JianYing draft name to create/overwrite")
    ap.add_argument("--main", help="Main audio/video file (optional). If omitted, background-only timeline is created.")
    ap.add_argument("--srt", help="Subtitle SRT file. If omitted, will transcribe from --main using faster-whisper.")
    ap.add_argument("--materials", nargs="+", required=True, help="Material files or folders (images/videos)")
    ap.add_argument("--aspect", choices=["9:16", "16:9"], default="9:16", help="Project aspect ratio")
    ap.add_argument("--style", default="", help="Style hint for Gemini matching (e.g. 口播/快剪/纪录片)")
    ap.add_argument(
        "--gemini-text-model",
        default=DEFAULT_GEMINI_TEXT_MODEL,
        help=f"Gemini CLI model for text tasks (matching). Default: {DEFAULT_GEMINI_TEXT_MODEL}. Use 'auto' to disable forcing.",
    )
    ap.add_argument(
        "--gemini-vision-model",
        default=DEFAULT_GEMINI_VISION_MODEL,
        help=f"Gemini CLI model for vision tasks (material analysis). Default: {DEFAULT_GEMINI_VISION_MODEL}. Use 'auto' to disable forcing.",
    )
    ap.add_argument("--gemini-model", default=None, help="(Deprecated) Same as --gemini-text-model")
    ap.add_argument("--cache-dir", default=None, help="Cache dir (default: <workspace>/.gemini_cache)")
    ap.add_argument("--limit-materials", type=int, default=0, help="Limit number of materials scanned/analyzed")
    ap.add_argument("--force", action="store_true", help="Force re-run Gemini analysis/matching (ignore cache)")
    ap.add_argument("--allow-reuse", action="store_true", help="Allow reuse of the same material id")
    ap.add_argument("--transition-duration", default="0.35s", help="Transition duration (e.g. 0.3s)")
    ap.add_argument("--mute-main-video", action="store_true", help="If --main is video, set its volume to 0")
    ap.add_argument("--whisper-model", default="small", help="faster-whisper model size (if transcribing)")
    ap.add_argument("--whisper-lang", default=None, help="Language code hint for whisper (e.g. zh)")
    args = ap.parse_args()

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from jy_wrapper import JyProject  # noqa: E402

    workspace_root = os.path.abspath(os.path.join(scripts_dir, "..", ".."))
    cache_dir = os.path.abspath(args.cache_dir or os.path.join(workspace_root, ".gemini_cache"))
    _ensure_dir(cache_dir)

    text_model = _normalize_model_arg(args.gemini_text_model or args.gemini_model)
    vision_model = _normalize_model_arg(args.gemini_vision_model)

    # 1) SRT
    if args.srt:
        srt_path = os.path.abspath(args.srt)
        if not os.path.exists(srt_path):
            raise SystemExit(f"SRT not found: {srt_path}")
    else:
        if not args.main:
            raise SystemExit("Need --srt or --main to generate subtitles.")
        main_path = os.path.abspath(args.main)
        if not os.path.exists(main_path):
            raise SystemExit(f"Main file not found: {main_path}")
        srt_path = os.path.join(cache_dir, f"transcribed_{_sha1(main_path)}.srt")
        if args.force or (not os.path.exists(srt_path)):
            print(f"[1/4] Transcribing -> {srt_path}")
            transcribe_to_srt_faster_whisper(
                main_path,
                srt_path,
                model_size=args.whisper_model,
                language=args.whisper_lang,
            )
        else:
            print(f"[1/4] Using cached SRT: {srt_path}")

    srt_items = parse_srt_content(_read_text(srt_path))
    if not srt_items:
        raise SystemExit("Failed to parse SRT or SRT is empty.")

    total_dur = max(it.end for it in srt_items)

    # 2) Collect + analyze materials
    print("[2/4] Collecting materials...")
    mat_files = collect_material_files(args.materials, limit=args.limit_materials)
    if not mat_files:
        raise SystemExit("No material files found.")
    print(f"  materials: {len(mat_files)}")

    print("[2/4] Analyzing materials with Gemini (images/storyboards) ...")
    materials = analyze_materials_with_gemini(
        mat_files,
        cache_dir=cache_dir,
        vision_model=vision_model,
        force=args.force,
    )

    # 3) Gemini match
    print("[3/4] Matching subtitles -> materials (Gemini) ...")
    matches = gemini_match_srt_to_materials(
        srt_items,
        materials,
        cache_dir=cache_dir,
        text_model=text_model,
        style_hint=args.style,
        force=args.force,
        allow_reuse=args.allow_reuse,
    )

    # 4) Assemble JianYing draft
    print("[4/4] Assembling JianYing draft ...")
    width, height = (1080, 1920) if args.aspect == "9:16" else (1920, 1080)
    project = JyProject(args.project, width=width, height=height, overwrite=True)

    # Background / main track
    if args.main:
        main_path = os.path.abspath(args.main)
        ext = os.path.splitext(main_path)[1].lower()
        if ext in VIDEO_EXTS or ext in IMAGE_EXTS:
            seg = project.add_media_safe(main_path, start_time="0s", track_name="Main")
            if seg and args.mute_main_video:
                seg.volume = 0.0
        elif ext in AUDIO_EXTS:
            project.add_color_strip("#000000", duration=f"{total_dur:.3f}s", track_name="Background")
            project.add_audio_safe(main_path, start_time="0s", track_name="MainAudio")
        else:
            project.add_color_strip("#000000", duration=f"{total_dur:.3f}s", track_name="Background")
    else:
        project.add_color_strip("#000000", duration=f"{total_dur:.3f}s", track_name="Background")

    # Subtitles
    project.import_subtitles(srt_path)

    # Build quick lookup for SRT items by idx (SRT idx is 1-based and may skip).
    sub_by_idx = {it.idx: it for it in srt_items}
    mat_by_id = {m.id: m for m in materials}

    broll_track = "Broll"
    added = 0
    for m in matches:
        try:
            srt_idx = int(m.get("srt_idx"))
        except Exception:
            continue
        sub = sub_by_idx.get(srt_idx)
        if not sub or not sub.text:
            continue

        mid = m.get("id", None)
        if mid is None:
            continue
        try:
            mid_int = int(mid)
        except Exception:
            continue

        mat = mat_by_id.get(mid_int)
        if not mat:
            continue

        start_time = f"{sub.start:.3f}s"
        duration = f"{sub.duration:.3f}s"
        seg = project.add_media_safe(mat.path, start_time=start_time, duration=duration, track_name=broll_track)
        if not seg:
            continue

        added += 1
        if added >= 2:
            tname = _sanitize_transition(m.get("transition"), ALLOWED_TRANSITIONS)
            try:
                project.add_transition_simple(str(tname), duration=args.transition_duration, track_name=broll_track)
            except Exception:
                # Non-fatal.
                pass

    project.save()
    print(f"Draft created: {args.project}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
