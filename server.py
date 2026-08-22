import json
import re
import subprocess
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path

INDEX_HTML = (Path(__file__).parent / "index.html").read_text()

STATE = {
    "total_visitors": 0,
    "sessions": {},
    "last_net": None,
}


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout


def get_cpu_load():
    load1, load5, load15 = (float(x) for x in run(["sysctl", "-n", "vm.loadavg"])
                             .strip("{}\n ").split()[:3])
    ncpu = int(run(["sysctl", "-n", "hw.ncpu"]).strip())
    return {
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
        "load_pct": round(min(load1 / ncpu * 100, 100), 1),
    }


def get_memory():
    total_bytes = int(run(["sysctl", "-n", "hw.memsize"]).strip())
    vm = run(["vm_stat"])
    page_size = int(re.search(r"page size of (\d+) bytes", vm).group(1))
    pages = {k: int(v) for k, v in re.findall(r"Pages (\w[\w ]*?):\s+(\d+)\.", vm)}
    used_pages = pages.get("active", 0) + pages.get("wired down", 0) + pages.get("occupied by compressor", 0)
    used_bytes = used_pages * page_size
    return {
        "used_pct": round(used_bytes / total_bytes * 100, 1),
        "used_gb": round(used_bytes / 1e9, 1),
        "total_gb": round(total_bytes / 1e9, 1),
    }


def get_disk():
    line = run(["df", "-k", "/"]).strip().splitlines()[-1]
    fields = line.split()
    total_kb, used_kb = int(fields[1]), int(fields[2])
    return {
        "used_pct": round(used_kb / total_kb * 100, 1),
        "used_gb": round(used_kb / 1e6, 1),
        "total_gb": round(total_kb / 1e6, 1),
    }


def get_network():
    out = run(["netstat", "-ib"]).splitlines()
    header = out[0].split()
    i_idx, o_idx = header.index("Ibytes"), header.index("Obytes")
    total_in = total_out = 0
    for line in out[1:]:
        fields = line.split()
        if len(fields) <= o_idx or "Link#" not in line:
            continue
        total_in += int(fields[i_idx])
        total_out += int(fields[o_idx])

    now = time.time()
    prev = STATE["last_net"]
    STATE["last_net"] = (now, total_in, total_out)
    if not prev:
        return {"rx_kbps": 0.0, "tx_kbps": 0.0}
    elapsed = max(now - prev[0], 0.001)
    return {
        "rx_kbps": round((total_in - prev[1]) / 1024 / elapsed, 1),
        "tx_kbps": round((total_out - prev[2]) / 1024 / elapsed, 1),
    }


def get_uptime():
    boot = run(["sysctl", "-n", "kern.boottime"])
    sec = int(re.search(r"sec = (\d+)", boot).group(1))
    return int(time.time() - sec)


def get_hardware():
    try:
        chip = run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or "Apple Silicon"
    except Exception:
        chip = "Apple Silicon"
    cores = int(run(["sysctl", "-n", "hw.ncpu"]).strip())
    ram_gb = round(int(run(["sysctl", "-n", "hw.memsize"]).strip()) / 1e9)
    return {"chip": chip, "cores": cores, "ram_gb": ram_gb}


def get_visitor(session_id):
    now = time.time()
    sessions = STATE["sessions"]
    if session_id not in sessions:
        STATE["total_visitors"] += 1
        sessions[session_id] = {"number": STATE["total_visitors"], "last_seen": now}
    else:
        sessions[session_id]["last_seen"] = now

    stale = [sid for sid, s in sessions.items() if now - s["last_seen"] > 5]
    for sid in stale:
        del sessions[sid]

    return {
        "your_number": sessions[session_id]["number"],
        "concurrent": len(sessions),
        "total": STATE["total_visitors"],
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _session_id(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" in cookie:
            return cookie["sid"].value, None
        new_sid = uuid.uuid4().hex
        return new_sid, new_sid

    def do_GET(self):
        sid, new_sid = self._session_id()

        if self.path == "/api/stats":
            body = json.dumps({
                "cpu": get_cpu_load(),
                "memory": get_memory(),
                "disk": get_disk(),
                "network": get_network(),
                "uptime_seconds": get_uptime(),
                "hardware": get_hardware(),
                "visitor": get_visitor(sid),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if new_sid:
                self.send_header("Set-Cookie", f"sid={new_sid}; Path=/; HttpOnly")
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/":
            body = INDEX_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            if new_sid:
                self.send_header("Set-Cookie", f"sid={new_sid}; Path=/; HttpOnly")
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    port = 8765
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"One Mac, Live running at http://localhost:{port}")
    server.serve_forever()
