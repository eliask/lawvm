from __future__ import annotations

import contextvars


_REPLAY_VERBOSE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "lawvm_finland_replay_verbose",
    default=True,
)


def set_replay_verbose(verbose: bool):
    return _REPLAY_VERBOSE.set(verbose)


def reset_replay_verbose(token) -> None:
    _REPLAY_VERBOSE.reset(token)


def replay_print(*args, **kwargs) -> None:
    if _REPLAY_VERBOSE.get():
        print(*args, **kwargs)
