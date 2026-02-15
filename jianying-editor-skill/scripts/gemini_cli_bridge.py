from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional


class GeminiCliError(RuntimeError):
    pass


@dataclass
class GeminiCliResult:
    session_id: Optional[str]
    response: str
    raw: dict
    stdout: str
    stderr: str
    returncode: int


def _ps_quote(s: str) -> str:
    # Single-quote for PowerShell literal string.
    return "'" + s.replace("'", "''") + "'"


def _extract_first_json_object(text: str) -> dict:
    text = text.strip()
    if not text:
        raise GeminiCliError("Gemini CLI returned empty stdout.")

    # Usually stdout is a single JSON object. Be defensive in case extra logs leak to stdout.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise GeminiCliError(f"Gemini CLI stdout is not JSON. Head: {text[:200]}")
    try:
        return json.loads(text[start : end + 1])
    except Exception as e:
        raise GeminiCliError(f"Failed to parse Gemini CLI JSON stdout: {e}. Head: {text[:200]}") from e


def run_gemini_cli(
    prompt: str,
    *,
    model: Optional[str] = None,
    output_format: str = "json",
    include_directories: Optional[list[str]] = None,
    stdin_text: Optional[str] = None,
    cwd: Optional[str] = None,
    timeout_s: int = 600,
) -> GeminiCliResult:
    """
    Call Gemini CLI in headless mode (non-interactive) and return the parsed result.

    Notes:
    - We write the prompt to a UTF-8 temp file to avoid Windows quoting/length issues.
    - stdout is expected to be a single JSON object when output_format="json".
    - stderr contains Gemini CLI logs; it is returned for debugging.
    """
    if include_directories is None:
        include_directories = []

    # Normalize and dedupe include dirs.
    norm_dirs: list[str] = []
    seen = set()
    for d in include_directories:
        if not d:
            continue
        nd = os.path.abspath(d)
        if nd not in seen:
            seen.add(nd)
            norm_dirs.append(nd)

    stdin_file = None
    if stdin_text is not None:
        # Feed large/complex payload via stdin to avoid Windows argv quoting issues.
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".stdin.txt") as f:
            stdin_file = f.name
            f.write(stdin_text)

    try:
        # A few retries help with transient capacity (429) errors.
        max_attempts = 3
        sleep_s = 5

        last_completed: Optional[subprocess.CompletedProcess[str]] = None
        for attempt in range(1, max_attempts + 1):
            ps_lines = []
            ps_lines.append("$ErrorActionPreference = 'Continue'")
            ps_lines.append(
                "$argsList = @('-p', %s, '-o', %s)"
                % (_ps_quote(prompt), _ps_quote(output_format))
            )
            if model:
                ps_lines.append("$argsList += @('-m', %s)" % _ps_quote(model))
            if norm_dirs:
                ps_lines.append("$inc = @(%s)" % (", ".join(_ps_quote(d) for d in norm_dirs)))
                ps_lines.append("foreach ($d in $inc) { $argsList += @('--include-directories', $d) }")
            if stdin_file:
                ps_lines.append(f"Get-Content -Raw -Encoding UTF8 {_ps_quote(stdin_file)} | & gemini @argsList")
            else:
                ps_lines.append("& gemini @argsList")

            last_completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", "\n".join(ps_lines)],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_s,
            )

            stdout = last_completed.stdout or ""
            stderr = last_completed.stderr or ""
            if last_completed.returncode == 0:
                # Some Gemini CLI setups return a "ready" bootstrap message on the first call
                # and ignore the actual prompt. Detect and retry once.
                if output_format == "json":
                    try:
                        raw = _extract_first_json_object(stdout)
                        resp = str(raw.get("response", "") or "").strip().lower()
                        bootstrap_markers = [
                            "ready for your first command",
                            "please provide your first command",
                            "waiting for your command",
                            "what can i do for you",
                            "what would you like me to do",
                            "i am ready for your first command",
                            "i'm ready for your first command",
                            "ready for your next command",
                            "我已准备好",
                            "请给出你的第一个指令",
                        ]
                        if any(m in resp for m in bootstrap_markers) and attempt < max_attempts:
                            continue
                    except Exception:
                        # If parsing fails here, fall through and let the normal parser handle it later.
                        pass
                break

            # Retry only on capacity / rate-limit signals.
            retryable = any(
                s in stderr
                for s in [
                    "status 429",
                    "code\": 429",
                    "Too Many Requests",
                    "No capacity",
                    "Retrying with backoff",
                ]
            )
            if (not retryable) or attempt == max_attempts:
                raise GeminiCliError(
                    "Gemini CLI failed "
                    f"(code={last_completed.returncode}). Stderr head: {stderr[:400]}"
                )

            # Backoff then retry.
            try:
                import time

                time.sleep(sleep_s)
            except Exception:
                pass
            sleep_s = min(sleep_s * 3, 60)

        if last_completed is None:
            raise GeminiCliError("Gemini CLI did not start.")

        stdout = last_completed.stdout or ""
        stderr = last_completed.stderr or ""

        if output_format == "json":
            raw = _extract_first_json_object(stdout)
            return GeminiCliResult(
                session_id=raw.get("session_id"),
                response=raw.get("response", ""),
                raw=raw,
                stdout=stdout,
                stderr=stderr,
                returncode=last_completed.returncode,
            )

        # text / stream-json: return raw stdout as response
        return GeminiCliResult(
            session_id=None,
            response=stdout,
            raw={},
            stdout=stdout,
            stderr=stderr,
            returncode=last_completed.returncode,
        )
    finally:
        if stdin_file:
            try:
                os.remove(stdin_file)
            except Exception:
                pass
