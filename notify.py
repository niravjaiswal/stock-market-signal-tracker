"""Alert delivery: colored terminal feed + macOS desktop notification."""
from __future__ import annotations

import shutil
import subprocess

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from config import cfg
from models import Signal

console = Console()

_ACTION_STYLE = {"BUY": "bold white on green", "SHORT": "bold white on red"}
_ACTION_EMOJI = {"BUY": "📈", "SHORT": "📉"}


def banner(text: str) -> None:
    console.print(Panel(text, border_style="cyan"))


def log_utterance(source: str, snippet: str) -> None:
    snippet = snippet.replace("\n", " ")
    if len(snippet) > 160:
        snippet = snippet[:157] + "…"
    console.print(f"[dim]· [{source}][/dim] {snippet}")


def emit(sig: Signal) -> None:
    """Print a signal to the terminal and fire a desktop notification."""
    style = _ACTION_STYLE.get(sig.action, "bold")
    emoji = _ACTION_EMOJI.get(sig.action, "•")
    head = Text()
    head.append(f" {sig.action} ", style=style)
    head.append(f" {sig.ticker} ", style="bold yellow")
    head.append(f"{sig.company}  ", style="white")
    head.append(f"conf {sig.confidence:.0%}", style="dim")

    body = Text()
    body.append(f"{emoji} {sig.rationale}\n", style="white")
    body.append(f"“{sig.quote}”\n", style="italic dim")
    body.append(f"source: {sig.source}", style="dim")
    if sig.url:
        body.append(f"  {sig.url}", style="dim blue")

    console.print(Panel(body, title=head, border_style=("green" if sig.action == "BUY" else "red")))

    if cfg.enable_desktop_notify:
        _desktop(sig, emoji)


def _desktop(sig: Signal, emoji: str) -> None:
    title = f"{emoji} {sig.action} {sig.ticker}"
    subtitle = f"{sig.company} · {sig.confidence:.0%}"
    msg = sig.rationale or sig.quote[:100]
    # Prefer terminal-notifier if installed (richer), else osascript.
    if shutil.which("terminal-notifier"):
        cmd = ["terminal-notifier", "-title", title, "-subtitle", subtitle, "-message", msg]
    else:
        script = (
            f'display notification {_q(msg)} with title {_q(title)} '
            f'subtitle {_q(subtitle)}'
        )
        cmd = ["osascript", "-e", script]
    try:
        # Fire-and-forget: emit() runs on the asyncio worker, so we must NOT
        # block the event loop waiting on the notifier. Detach and move on.
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass  # never let a notification failure break the pipeline


def _q(s: str) -> str:
    """Quote a string for AppleScript."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
