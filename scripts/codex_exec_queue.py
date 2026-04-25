#!/usr/bin/env python3
"""Run `codex exec` over a queue of prompt files with durable logs.

The runner is intentionally simple and durable:

- one fresh `codex exec` process per queue item
- prompt text is read from a file and piped over stdin
- JSONL event stream is captured per task
- final assistant message is captured per task
- exit code, timestamps, and prompt path are summarized at the end

This is a better fit for unattended overnight work than one giant agent thread:
bounded tasks fail independently, logs remain inspectable, and a bad late task
does not poison all earlier work.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def natural_sort_key(value: str) -> list[object]:
    parts = re.split(r"(\d+)", value)
    key: list[object] = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.lower())
    return key


def discover_prompt_files(queue_path: Path, pattern: str) -> list[Path]:
    if queue_path.is_dir():
        files = [p for p in queue_path.glob(pattern) if p.is_file()]
        return sorted(files, key=lambda p: natural_sort_key(p.name))

    if queue_path.is_file():
        entries: list[Path] = []
        base = queue_path.parent
        for raw in queue_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            path = Path(line)
            if not path.is_absolute():
                path = (base / path).resolve()
            entries.append(path)
        return entries

    raise FileNotFoundError(f"Queue path not found: {queue_path}")


@dataclass
class TaskResult:
    index: int
    prompt_file: str
    task_dir: str
    started_at: str
    finished_at: str
    duration_seconds: float
    exit_code: int
    status: str
    thread_id: str | None


def parse_thread_id(jsonl_path: Path) -> str | None:
    if not jsonl_path.exists():
        return None
    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    return None


def write_summary(run_dir: Path, results: Iterable[TaskResult]) -> None:
    rows = [asdict(result) for result in results]
    (run_dir / "summary.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "index,status,exit_code,duration_seconds,thread_id,prompt_file,task_dir",
    ]
    for row in rows:
        lines.append(
            ",".join(
                [
                    str(row["index"]),
                    str(row["status"]),
                    str(row["exit_code"]),
                    f"{row['duration_seconds']:.2f}",
                    str(row["thread_id"] or ""),
                    json.dumps(row["prompt_file"], ensure_ascii=True),
                    json.dumps(row["task_dir"], ensure_ascii=True),
                ]
            )
        )
    (run_dir / "summary.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_exec_command(args: argparse.Namespace, final_message_path: Path) -> list[str]:
    cmd = ["codex", "exec"]
    if args.dangerously_bypass_approvals_and_sandbox:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    elif args.full_auto:
        cmd.append("--full-auto")
    else:
        cmd.extend(["--sandbox", args.sandbox])

    cmd.extend(["--cd", str(args.workdir)])
    cmd.extend(["--output-last-message", str(final_message_path)])
    cmd.append("--json")

    if args.model:
        cmd.extend(["--model", args.model])
    if args.profile:
        cmd.extend(["--profile", args.profile])
    if args.color:
        cmd.extend(["--color", args.color])
    if args.skip_git_repo_check:
        cmd.append("--skip-git-repo-check")
    for add_dir in args.add_dir:
        cmd.extend(["--add-dir", str(add_dir)])
    for config in args.config:
        cmd.extend(["--config", config])

    cmd.append("-")
    return cmd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run `codex exec` over a queue of prompt files.",
    )
    parser.add_argument(
        "queue",
        type=Path,
        help="Queue directory or manifest file. A manifest lists one prompt-file path per line.",
    )
    parser.add_argument(
        "--pattern",
        default="*.md",
        help="Glob pattern when QUEUE is a directory. Default: %(default)s",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path.cwd(),
        help="Codex working directory. Default: current directory.",
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path(".tmp/codex_queue_runs"),
        help="Directory under which run logs are stored. Default: %(default)s",
    )
    parser.add_argument(
        "--label",
        default="queue",
        help="Short label used in the run directory name.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional Codex model override.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Optional Codex profile from ~/.codex/config.toml.",
    )
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        default="workspace-write",
        help="Sandbox mode for `codex exec` when not using --full-auto. Default: %(default)s",
    )
    parser.add_argument(
        "--full-auto",
        action="store_true",
        help="Pass `--full-auto` to `codex exec` instead of explicit --sandbox.",
    )
    parser.add_argument(
        "--dangerously-bypass-approvals-and-sandbox",
        action="store_true",
        help="Pass the dangerous bypass flag through to `codex exec`. Use only inside an external sandbox.",
    )
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        help="Repeatable `codex exec --config key=value` override.",
    )
    parser.add_argument(
        "--add-dir",
        action="append",
        type=Path,
        default=[],
        help="Repeatable writable directory passed through as `--add-dir`.",
    )
    parser.add_argument(
        "--color",
        choices=("always", "never", "auto"),
        default="never",
        help="Color mode for `codex exec`. Default: %(default)s",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help="Optional timeout per task.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue processing later tasks if one task exits non-zero or times out.",
    )
    parser.add_argument(
        "--skip-git-repo-check",
        action="store_true",
        help="Pass through to `codex exec`.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved queue and run directory, then exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue_path = args.queue.resolve()
    workdir = args.workdir.resolve()

    if shutil.which("codex") is None:
        print("ERROR: `codex` not found on PATH", file=sys.stderr)
        return 2

    prompt_files = discover_prompt_files(queue_path, args.pattern)
    if not prompt_files:
        print("ERROR: no prompt files found", file=sys.stderr)
        return 2

    run_dir = (
        args.run_root.resolve()
        / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.label}"
    )

    print(f"queue:      {queue_path}")
    print(f"workdir:    {workdir}")
    print(f"run_dir:    {run_dir}")
    print(f"tasks:      {len(prompt_files)}")
    for idx, prompt_path in enumerate(prompt_files, start=1):
        print(f"  {idx:03d} {prompt_path}")

    if args.dry_run:
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    results: list[TaskResult] = []

    for idx, prompt_path in enumerate(prompt_files, start=1):
        if not prompt_path.exists():
            print(f"[{idx:03d}] missing prompt file: {prompt_path}", file=sys.stderr)
            result = TaskResult(
                index=idx,
                prompt_file=str(prompt_path),
                task_dir="",
                started_at=utc_now(),
                finished_at=utc_now(),
                duration_seconds=0.0,
                exit_code=2,
                status="missing_prompt",
                thread_id=None,
            )
            results.append(result)
            if not args.continue_on_error:
                write_summary(run_dir, results)
                return 2
            continue

        task_name = f"{idx:03d}_{prompt_path.stem}"
        task_dir = run_dir / task_name
        task_dir.mkdir(parents=True, exist_ok=True)

        prompt_text = prompt_path.read_text(encoding="utf-8")
        (task_dir / "prompt.md").write_text(prompt_text, encoding="utf-8")
        (task_dir / "prompt_path.txt").write_text(str(prompt_path) + "\n", encoding="utf-8")

        stdout_path = task_dir / "events.jsonl"
        stderr_path = task_dir / "stderr.log"
        final_message_path = task_dir / "final_message.txt"
        meta_path = task_dir / "meta.json"

        cmd = build_exec_command(args, final_message_path)

        started_at = utc_now()
        start_monotonic = datetime.now().timestamp()
        exit_code = 0
        status = "ok"

        print(f"[{idx:03d}/{len(prompt_files):03d}] running {prompt_path.name}")

        with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_file:
            try:
                proc = subprocess.run(
                    cmd,
                    input=prompt_text,
                    text=True,
                    cwd=workdir,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=args.timeout_seconds,
                    check=False,
                )
                exit_code = proc.returncode
                if exit_code != 0:
                    status = "failed"
            except subprocess.TimeoutExpired:
                exit_code = 124
                status = "timeout"
                stderr_file.write(
                    f"\nTIMEOUT after {args.timeout_seconds} seconds at {utc_now()}\n"
                )

        finished_at = utc_now()
        duration_seconds = datetime.now().timestamp() - start_monotonic
        thread_id = parse_thread_id(stdout_path)

        meta = {
            "index": idx,
            "prompt_file": str(prompt_path),
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": round(duration_seconds, 2),
            "exit_code": exit_code,
            "status": status,
            "thread_id": thread_id,
            "command": cmd,
            "workdir": str(workdir),
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

        result = TaskResult(
            index=idx,
            prompt_file=str(prompt_path),
            task_dir=str(task_dir),
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
            exit_code=exit_code,
            status=status,
            thread_id=thread_id,
        )
        results.append(result)

        print(
            f"[{idx:03d}/{len(prompt_files):03d}] {status} exit={exit_code} "
            f"duration={duration_seconds:.1f}s"
        )

        if status != "ok" and not args.continue_on_error:
            break

    write_summary(run_dir, results)

    failed = [r for r in results if r.status != "ok"]
    if failed:
        print(f"completed with failures: {len(failed)} task(s) not ok", file=sys.stderr)
        return 1

    print("completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
