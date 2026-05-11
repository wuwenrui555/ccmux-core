"""Web viewer for ccmux-core: SSE-backed live tail in a browser.

Spawns a Backend for one tmux session and serves:

* ``GET /`` — a self-contained HTML page that opens an EventSource
  against ``/stream``.
* ``GET /stream`` — ``text/event-stream`` pushing one event per
  observation (state / event / message / spinner). Each SSE
  payload is a JSON object with ``stream``, ``label``, ``body``
  fields plus standard metadata (timestamp, sid, tmux@window).

Designed for Tailscale-tailnet access: bind to ``0.0.0.0`` and any
tailnet device can browse to ``http://<this-host>:<port>/``.

Pure stdlib (asyncio HTTP); no third-party web framework.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from . import __version__
from .backend import Backend
from .discover import list_live_tmux_bindings
from .state import Blocked, Dead, Idle, State, Working

logger = logging.getLogger(__name__)

_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ccmux-core · {session}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --bg:        #1a1a1a;
    --fg:        #ddd;
    --sep:       #555;
    --hdr:       #6cf;
    --idle:      #888;
    --working:   #7d7;
    --blocked:   #fc6;
    --dead:      #f77;
    --user:      #6cf;
    --assistant: #ff8;
    --event:     #aaa;
    --spinner:   #aaf;
  }}
  body {{
    font: 14px/1.45 ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    margin: 0;
    padding: 0.8rem 1rem 4rem;
    background: var(--bg);
    color: var(--fg);
  }}
  h1 {{
    font-size: 14px;
    color: var(--hdr);
    margin: 0 0 0.5rem;
    font-weight: normal;
  }}
  .block {{ margin-bottom: 0.9rem; }}
  .sep {{ color: var(--sep); white-space: pre; }}
  .header {{ color: var(--hdr); }}
  .body {{
    white-space: pre-wrap;
    word-break: break-word;
    margin-top: 2px;
  }}
  .label {{ font-weight: bold; }}
  .label.idle      {{ color: var(--idle); }}
  .label.working   {{ color: var(--working); }}
  .label.blocked   {{ color: var(--blocked); }}
  .label.dead      {{ color: var(--dead); }}
  .label.user      {{ color: var(--user); }}
  .label.assistant {{ color: var(--assistant); }}
  .label.event     {{ color: var(--event); }}
  .label.spinner   {{ color: var(--spinner); }}
  #status {{
    position: fixed; bottom: 0; left: 0; right: 0;
    padding: 4px 8px;
    background: #111;
    color: var(--sep);
    font-size: 12px;
    border-top: 1px solid #333;
  }}
  #status.connected {{ color: var(--working); }}
  #status.broken    {{ color: var(--dead); }}
</style>
</head>
<body>
<h1>ccmux-core watch · session={session} · pane={pane}</h1>
<div id="log"></div>
<div id="status">connecting…</div>
<script>
(function() {{
  const log = document.getElementById('log');
  const status = document.getElementById('status');
  const sepWidth = 100;
  let autoScroll = true;
  window.addEventListener('scroll', () => {{
    autoScroll = window.scrollY + window.innerHeight + 50 >= document.body.scrollHeight;
  }});
  function renderBlock(rec) {{
    const block = document.createElement('div');
    block.className = 'block';
    const sepLine = '─ ' + rec.emit_time + ' ' + '─'.repeat(Math.max(0, sepWidth - 4 - rec.emit_time.length));
    const sep = document.createElement('div');
    sep.className = 'sep';
    sep.textContent = sepLine;
    const hdr = document.createElement('div');
    hdr.className = 'header';
    const labelClass = rec.label_class || '';
    hdr.innerHTML = '[ ' + escapeHtml(rec.ts) + ' ' + escapeHtml(rec.tmux_frag) +
      ' ' + escapeHtml(rec.sid8) + ' ] <span class="label ' + labelClass + '">' +
      escapeHtml(rec.label) + '</span>';
    const body = document.createElement('div');
    body.className = 'body';
    body.textContent = rec.body;
    block.appendChild(sep);
    block.appendChild(hdr);
    block.appendChild(body);
    log.appendChild(block);
    if (autoScroll) window.scrollTo(0, document.body.scrollHeight);
  }}
  function escapeHtml(s) {{
    return String(s).replace(/[&<>"']/g, c => ({{
      '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
    }}[c]));
  }}
  const ev = new EventSource('/stream');
  ev.onopen = () => {{
    status.textContent = 'connected';
    status.className = 'connected';
  }};
  ev.onerror = () => {{
    status.textContent = 'disconnected — refresh to reconnect';
    status.className = 'broken';
  }};
  ev.onmessage = (e) => {{
    try {{ renderBlock(JSON.parse(e.data)); }} catch (err) {{ console.error(err); }}
  }};
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Block formatting (shared shape between CLI pretty mode and SSE)
# ---------------------------------------------------------------------------


def _now() -> str:
    n = datetime.now(UTC)
    return n.strftime("%H:%M:%S") + f".{n.microsecond // 1000:03d}"


def _ts_short(ts: str | None) -> str:
    if not ts:
        return datetime.now(UTC).strftime("%H:%M:%S")
    raw = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        return datetime.fromisoformat(raw).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return ts[11:19] if len(ts) >= 19 else ts[:8]


def _state_label_body(
    state: State, latest_spinner_text: str | None
) -> tuple[str, str, str]:
    label = type(state).__name__.upper()
    label_class = label.lower()
    if isinstance(state, Idle):
        body = f"reason={state.reason}"
    elif isinstance(state, Working):
        parts: list[str] = []
        if state.tool_name is not None:
            parts.append(f"tool={state.tool_name}")
        if latest_spinner_text:
            parts.append(f"spinner={latest_spinner_text}")
        body = " · ".join(parts) if parts else "(starting)"
    elif isinstance(state, Blocked):
        body = f"kind={state.kind} · tool={state.tool_name}"
    elif isinstance(state, Dead):
        body = f"reason={state.reason}"
        if state.detail:
            body += f" · detail={state.detail}"
    else:
        body = "?"
    return label, body, label_class


def _event_label_body(event: dict) -> tuple[str, str, str]:
    et = event.get("event_type", "?")
    payload = event.get("payload") or {}
    label = f"EVENT · {et}"
    if et == "user_prompt_submit":
        body = json.dumps(payload.get("prompt", ""), ensure_ascii=False)[1:-1]
    elif et in ("pre_tool_use", "post_tool_use", "permission_request"):
        body = f"tool={payload.get('tool_name', '?')}"
    elif et == "notification":
        body = json.dumps(payload.get("message", ""), ensure_ascii=False)[1:-1]
    elif et == "stop":
        last = payload.get("last_assistant_message", "")
        body = json.dumps(last, ensure_ascii=False)[1:-1]
    elif et == "session_end":
        body = f"reason={payload.get('reason', '')!r}"
    else:
        body = ""
    return label, body, "event"


def _message_label_body(msg) -> tuple[str, str, str]:
    parts = [(msg.role or "?").upper()]
    if msg.content_type and msg.content_type != "text":
        parts.append(msg.content_type)
    if msg.tool_name:
        parts.append(msg.tool_name)
    label = " · ".join(parts)
    body = json.dumps(msg.text or "", ensure_ascii=False)[1:-1]
    if msg.image_data:
        n = len(msg.image_data)
        body += f" [+{n} image{'s' if n != 1 else ''}]"
    label_class = (msg.role or "").lower() or "event"
    return label, body, label_class


def _spinner_label_body(activity) -> tuple[str, str, str]:
    if activity is None:
        return "SPINNER · none", "", "spinner"
    label = f"SPINNER · {type(activity).__name__}"
    body = activity.text
    todos = getattr(activity, "todos", ()) or ()
    if todos:
        body += f" · todos={list(todos)}"
    return label, body, "spinner"


# ---------------------------------------------------------------------------
# Backend → SSE broadcast hub
# ---------------------------------------------------------------------------


class _Hub:
    """One Backend, many SSE subscribers (fan-out).

    Each subscriber gets its own asyncio.Queue; the pump tasks fan
    each observation out to every queue. Slow subscribers don't
    block fast ones (each queue grows independently; if it's
    pathological we cap at 1000 and drop oldest).
    """

    _Q_MAX = 1000

    def __init__(
        self, tmux_session: str, pane_id: str, primary_sid: str, window_id: str
    ):
        self.tmux_session = tmux_session
        self.pane_id = pane_id
        self.primary_sid = primary_sid
        self.window_id = window_id
        self._subscribers: set[asyncio.Queue] = set()
        self._latest_spinner_text: str | None = None
        self._backend: Backend | None = None
        self._tasks: list[asyncio.Task] = []
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        self._backend = Backend(tmux_session=self.tmux_session, pane_id=self.pane_id)
        await self._backend.__aenter__()
        self._tasks = [
            asyncio.create_task(self._pump_states()),
            asyncio.create_task(self._pump_events()),
            asyncio.create_task(self._pump_messages()),
            asyncio.create_task(self._pump_spinners()),
        ]

    async def stop(self) -> None:
        self._stopped.set()
        for t in self._tasks:
            if not t.done():
                t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if self._backend is not None:
            await self._backend.__aexit__(None, None, None)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._Q_MAX)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _broadcast(self, rec: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(rec)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(rec)
                except asyncio.QueueFull:
                    pass

    def _build_rec(
        self, *, label: str, body: str, label_class: str, ts: str | None
    ) -> dict:
        return {
            "emit_time": _now(),
            "ts": _ts_short(ts),
            "tmux_frag": f"{self.tmux_session}{self.window_id or ''}",
            "sid8": (self.primary_sid or "")[:8] or "--------",
            "label": label,
            "label_class": label_class,
            "body": body,
        }

    async def _pump_states(self) -> None:
        assert self._backend is not None
        async for s in self._backend.states():
            label, body, label_class = _state_label_body(s, self._latest_spinner_text)
            self._broadcast(
                self._build_rec(
                    label=label, body=body, label_class=label_class, ts=None
                )
            )

    async def _pump_events(self) -> None:
        assert self._backend is not None
        async for ev in self._backend.events():
            sid = (ev.get("claude") or {}).get("session_id")
            if sid:
                self.primary_sid = sid
            label, body, label_class = _event_label_body(ev)
            self._broadcast(
                self._build_rec(
                    label=label,
                    body=body,
                    label_class=label_class,
                    ts=ev.get("timestamp"),
                )
            )

    async def _pump_messages(self) -> None:
        assert self._backend is not None
        async for msg in self._backend.messages():
            label, body, label_class = _message_label_body(msg)
            self._broadcast(
                self._build_rec(
                    label=label,
                    body=body,
                    label_class=label_class,
                    ts=msg.timestamp,
                )
            )

    async def _pump_spinners(self) -> None:
        assert self._backend is not None
        async for a in self._backend.spinners():
            if a is not None and hasattr(a, "text"):
                self._latest_spinner_text = a.text
            elif a is None:
                self._latest_spinner_text = None
            label, body, label_class = _spinner_label_body(a)
            self._broadcast(
                self._build_rec(
                    label=label, body=body, label_class=label_class, ts=None
                )
            )


# ---------------------------------------------------------------------------
# Minimal asyncio HTTP server (stdlib only)
# ---------------------------------------------------------------------------


async def _read_request_line(reader: asyncio.StreamReader) -> tuple[str, str] | None:
    """Read just enough to know the verb + path. Discard the rest of headers."""
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    except (TimeoutError, asyncio.IncompleteReadError):
        return None
    if not line:
        return None
    parts = line.decode("latin-1", errors="replace").split()
    if len(parts) < 2:
        return None
    verb, path = parts[0], parts[1]
    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        except (TimeoutError, asyncio.IncompleteReadError):
            return verb, path
        if line in (b"\r\n", b"\n", b""):
            return verb, path


async def _serve_html(writer: asyncio.StreamWriter, hub: _Hub) -> None:
    html = _HTML.format(session=hub.tmux_session, pane=hub.pane_id).encode("utf-8")
    head = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"Cache-Control: no-store\r\n"
        b"Content-Length: " + str(len(html)).encode() + b"\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    writer.write(head + html)
    await writer.drain()


async def _serve_stream(writer: asyncio.StreamWriter, hub: _Hub, peer: str) -> None:
    head = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/event-stream; charset=utf-8\r\n"
        b"Cache-Control: no-store\r\n"
        b"Connection: keep-alive\r\n"
        b"X-Accel-Buffering: no\r\n"
        b"\r\n"
        # initial comment to flush headers through any proxy
        b": ccmux-core-web stream connected\n\n"
    )
    writer.write(head)
    await writer.drain()

    q = hub.subscribe()
    logger.info(
        "SSE subscriber connected from %s (total=%d)", peer, len(hub._subscribers)
    )
    try:
        while True:
            try:
                rec = await asyncio.wait_for(q.get(), timeout=15.0)
            except TimeoutError:
                # keep-alive comment
                writer.write(b": keep-alive\n\n")
                await writer.drain()
                continue
            payload = "data: " + json.dumps(rec, ensure_ascii=False) + "\n\n"
            writer.write(payload.encode("utf-8"))
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        return
    finally:
        hub.unsubscribe(q)
        logger.info(
            "SSE subscriber disconnected from %s (total=%d)",
            peer,
            len(hub._subscribers),
        )


async def _serve_404(writer: asyncio.StreamWriter) -> None:
    body = b"not found\n"
    head = (
        b"HTTP/1.1 404 Not Found\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    writer.write(head + body)
    await writer.drain()


def _make_handler(hub: _Hub):
    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = "?"
        try:
            sockname = writer.get_extra_info("peername")
            if sockname:
                peer = f"{sockname[0]}:{sockname[1]}"
            req = await _read_request_line(reader)
            if req is None:
                return
            verb, path = req
            if verb != "GET":
                await _serve_404(writer)
                return
            # Strip query string for routing.
            route = path.split("?", 1)[0]
            if route == "/":
                await _serve_html(writer, hub)
            elif route == "/stream":
                await _serve_stream(writer, hub, peer)
            else:
                await _serve_404(writer)
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            logger.exception("handler crashed for %s", peer)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError, Exception):
                pass

    return handler


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def serve_web(
    tmux_session: str,
    *,
    host: str = "0.0.0.0",
    port: int = 8765,
) -> int:
    """Run the web viewer for one tmux session until interrupted."""
    bindings = list_live_tmux_bindings()
    match = next((b for b in bindings if b.tmux_session == tmux_session), None)
    if match is None:
        print(
            f"ccmux-core: no live tmux session named {tmux_session!r}",
            flush=True,
        )
        return 1

    hub = _Hub(
        tmux_session=match.tmux_session,
        pane_id=match.pane_id,
        primary_sid=match.primary_session_id,
        window_id=match.window_id,
    )
    await hub.start()

    server = await asyncio.start_server(_make_handler(hub), host=host, port=port)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(
        f"ccmux-core web · v{__version__} · serving tmux={tmux_session} pane={match.pane_id}",
        flush=True,
    )
    print(f"  listening on {addrs}", flush=True)
    print(f"  open http://<this-host>:{port}/ from any tailnet device", flush=True)
    try:
        async with server:
            await server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        await hub.stop()
    return 0
