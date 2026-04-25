#!/usr/bin/env python3
"""Drive one long-running Codex campaign via repeated `codex exec resume`.

This runner creates one initial Codex session, then keeps resuming that exact
session until the assistant ends a turn with a run-specific sentinel line:

    __CODEX_QUEUE_SENTINEL__::<run_id>::CONTINUE
    __CODEX_QUEUE_SENTINEL__::<run_id>::DONE

The per-run nonce makes accidental sentinel collisions effectively impossible.

oneliner:
SID="" PROMPT="continue working on finland or core lawvm - pick the best things to work on and continue working on these, always leading ever closer to the ultimate lawvm vision. important to keep specs and docs up to date so other AIs know to continue the work later." time for i in $(seq 1..1000); do time codex exec resume --json --full-auto -o ../lawvm.out.$i.last "$SID" "$PROMPT" | tee ../lawvm.out.$i.jsonl; done
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import textwrap
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def default_continue_prompt(done: str, cont: str) -> str:
    return textwrap.dedent(
        f"""\
        Continue from the current repo state and your prior session state.

        Rules:
        - Work the next bounded queued item only.
        - Keep edits and verification scoped to that item.
        - If the full campaign is complete, the final non-empty line must be exactly:
          {done}
        - Otherwise, the final non-empty line must be exactly:
          {cont}
        - Never emit either sentinel anywhere except as the final non-empty line.
        """
    )


def with_control_block(user_prompt: str, done: str, cont: str) -> str:
    control = textwrap.dedent(
        f"""

        [Codex Resume Loop Control]
        The final non-empty line of your reply must be exactly one of:
        {done}
        {cont}
        Use {done} only when the entire campaign is complete.
        Use {cont} otherwise.
        Never emit either sentinel anywhere else in the reply.
        """
    ).strip()
    return user_prompt.rstrip() + "\n\n" + control + "\n"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_thread_id(events_path: Path) -> str | None:
    if not events_path.exists():
        return None
    for raw in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith("{"):
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


def last_nonempty_line(path: Path) -> str | None:
    if not path.exists():
        return None
    lines = [line.rstrip("\n\r") for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    for line in reversed(lines):
        if line.strip():
            return line
    return None


@dataclass
class TurnResult:
    turn: int
    kind: str
    started_at: str
    finished_at: str
    duration_seconds: float
    exit_code: int
    status: str
    thread_id: str | None
    sentinel: str | None
    turn_dir: str


def write_summary(run_dir: Path, results: list[TurnResult]) -> None:
    rows = [asdict(result) for result in results]
    (run_dir / "summary.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def build_initial_command(
    args: argparse.Namespace,
    final_message_path: Path,
) -> list[str]:
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
    for config in args.config:
        cmd.extend(["--config", config])
    for add_dir in args.add_dir:
        cmd.extend(["--add-dir", str(add_dir)])
    cmd.extend(["--color", args.color])
    cmd.append("-")
    return cmd


def build_resume_command(
    args: argparse.Namespace,
    thread_id: str,
    final_message_path: Path,
) -> list[str]:
    cmd = ["codex", "exec", "resume", thread_id]
    if args.dangerously_bypass_approvals_and_sandbox:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    elif args.full_auto:
        cmd.append("--full-auto")
    cmd.extend(["--output-last-message", str(final_message_path)])
    cmd.append("--json")
    if args.model:
        cmd.extend(["--model", args.model])
    if args.profile:
        cmd.extend(["--profile", args.profile])
    for config in args.config:
        cmd.extend(["--config", config])
    cmd.extend(["--color", args.color])
    cmd.append("-")
    return cmd


def run_one(
    cmd: list[str],
    prompt_text: str,
    workdir: Path,
    timeout_seconds: int | None,
    events_path: Path,
    stderr_path: Path,
) -> int:
    with events_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_file:
        proc = subprocess.run(
            cmd,
            input=prompt_text,
            text=True,
            cwd=workdir,
            stdout=stdout_file,
            stderr=stderr_file,
            timeout=timeout_seconds,
            check=False,
        )
    return proc.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Codex resume loop with an exact sentinel.")
    parser.add_argument("initial_prompt", type=Path, help="Initial campaign prompt file.")
    parser.add_argument(
        "--continue-prompt-file",
        type=Path,
        default=None,
        help="Optional continue prompt template file. If omitted, a built-in prompt is used.",
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
        default=Path(".tmp/codex_resume_runs"),
        help="Directory for logs and metadata. Default: %(default)s",
    )
    parser.add_argument("--label", default="resume_loop", help="Short label for the run directory.")
    parser.add_argument("--run-id", default=None, help="Optional fixed run id. Default: random UUID hex.")
    parser.add_argument("--model", default=None, help="Optional Codex model override.")
    parser.add_argument("--profile", default=None, help="Optional Codex profile.")
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        default="workspace-write",
        help="Sandbox mode for the initial `codex exec` call. Default: %(default)s",
    )
    parser.add_argument("--full-auto", action="store_true", help="Pass `--full-auto` to Codex.")
    parser.add_argument(
        "--dangerously-bypass-approvals-and-sandbox",
        action="store_true",
        help="Pass the dangerous bypass flag through to Codex.",
    )
    parser.add_argument("--config", action="append", default=[], help="Repeatable `codex --config key=value`.")
    parser.add_argument(
        "--add-dir",
        action="append",
        type=Path,
        default=[],
        help="Repeatable `--add-dir` for the initial exec call.",
    )
    parser.add_argument(
        "--color",
        choices=("always", "never", "auto"),
        default="never",
        help="Color mode for Codex output. Default: %(default)s",
    )
    parser.add_argument("--timeout-seconds", type=int, default=None, help="Optional timeout per turn.")
    parser.add_argument("--max-turns", type=int, default=50, help="Maximum turns before aborting.")
    parser.add_argument("--dry-run", action="store_true", help="Print resolved settings and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if shutil.which("codex") is None:
        print("ERROR: `codex` not found on PATH", file=sys.stderr)
        return 2

    workdir = args.workdir.resolve()
    initial_prompt_path = args.initial_prompt.resolve()
    if not initial_prompt_path.exists():
        print(f"ERROR: initial prompt not found: {initial_prompt_path}", file=sys.stderr)
        return 2

    run_id = args.run_id or uuid.uuid4().hex
    done = f"__CODEX_QUEUE_SENTINEL__::{run_id}::DONE"
    cont = f"__CODEX_QUEUE_SENTINEL__::{run_id}::CONTINUE"

    run_dir = args.run_root.resolve() / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.label}"
    print(f"workdir:    {workdir}")
    print(f"run_dir:    {run_dir}")
    print(f"run_id:     {run_id}")
    print(f"done:       {done}")
    print(f"continue:   {cont}")
    print(f"max_turns:  {args.max_turns}")

    if args.dry_run:
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    results: list[TurnResult] = []
    metadata = {
        "created_at": utc_now(),
        "workdir": str(workdir),
        "initial_prompt": str(initial_prompt_path),
        "continue_prompt_file": str(args.continue_prompt_file.resolve()) if args.continue_prompt_file else None,
        "run_id": run_id,
        "done_sentinel": done,
        "continue_sentinel": cont,
        "max_turns": args.max_turns,
    }
    (run_dir / "run_meta.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    initial_user_prompt = read_text(initial_prompt_path)
    initial_prompt_text = with_control_block(initial_user_prompt, done, cont)
    continue_user_prompt = (
        read_text(args.continue_prompt_file.resolve())
        if args.continue_prompt_file
        else default_continue_prompt(done, cont)
    )
    continue_prompt_text = with_control_block(continue_user_prompt, done, cont)

    thread_id: str | None = None

    for turn in range(1, args.max_turns + 1):
        turn_dir = run_dir / f"turn_{turn:03d}"
        turn_dir.mkdir(parents=True, exist_ok=True)

        prompt_text = initial_prompt_text if turn == 1 else continue_prompt_text
        prompt_kind = "initial" if turn == 1 else "resume"
        (turn_dir / "prompt.md").write_text(prompt_text, encoding="utf-8")

        final_message_path = turn_dir / "final_message.txt"
        events_path = turn_dir / "events.jsonl"
        stderr_path = turn_dir / "stderr.log"

        if turn == 1:
            cmd = build_initial_command(args, final_message_path)
        else:
            assert thread_id is not None
            cmd = build_resume_command(args, thread_id, final_message_path)
        (turn_dir / "command.json").write_text(json.dumps(cmd, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

        started_at = utc_now()
        started_ts = datetime.now().timestamp()
        print(f"[turn {turn:03d}] {prompt_kind}")

        try:
            exit_code = run_one(
                cmd=cmd,
                prompt_text=prompt_text,
                workdir=workdir,
                timeout_seconds=args.timeout_seconds,
                events_path=events_path,
                stderr_path=stderr_path,
            )
            status = "ok" if exit_code == 0 else "failed"
        except subprocess.TimeoutExpired:
            exit_code = 124
            status = "timeout"
            stderr_path.write_text(
                f"TIMEOUT after {args.timeout_seconds} seconds at {utc_now()}\n",
                encoding="utf-8",
            )

        finished_at = utc_now()
        duration_seconds = datetime.now().timestamp() - started_ts

        if turn == 1:
            thread_id = parse_thread_id(events_path)

        sentinel = last_nonempty_line(final_message_path)
        if status == "ok":
            if turn == 1 and not thread_id:
                status = "missing_thread_id"
            elif sentinel not in {done, cont}:
                status = "invalid_sentinel"

        result = TurnResult(
            turn=turn,
            kind=prompt_kind,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
            exit_code=exit_code,
            status=status,
            thread_id=thread_id,
            sentinel=sentinel,
            turn_dir=str(turn_dir),
        )
        results.append(result)
        write_summary(run_dir, results)

        print(
            f"[turn {turn:03d}] status={status} exit={exit_code} "
            f"duration={duration_seconds:.1f}s sentinel={sentinel!r}"
        )

        if status != "ok":
            return 1
        if sentinel == done:
            print("campaign finished")
            return 0

    print("max turns reached without DONE sentinel", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
