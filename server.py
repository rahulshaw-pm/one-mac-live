import json
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse, parse_qs

INDEX_HTML = (Path(__file__).parent / "index.html").read_text()

STATE = {
    "total_visitors": 0,
    "sessions": {},
    "last_net": None,
}

WIN_LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
OPPONENT_TIMEOUT = 6  # seconds without a poll before we consider a player gone

GAME_LOCK = threading.Lock()
GAME = {
    "waiting": None,       # sid of a player waiting for an opponent
    "player_game": {},     # sid -> game_id
    "games": {},           # game_id -> game state
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


def check_winner(board):
    for a, b, c in WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if all(board) else None


def _cleanup_stale_game(gid):
    game = GAME["games"].get(gid)
    if not game:
        return
    now = time.time()
    for sid in list(game["players"]):
        if now - game["last_seen"].get(sid, 0) > OPPONENT_TIMEOUT * 3:
            GAME["games"].pop(gid, None)
            for s in list(game["players"]):
                if GAME["player_game"].get(s) == gid:
                    GAME["player_game"].pop(s, None)
            return


def game_join(sid):
    with GAME_LOCK:
        gid = GAME["player_game"].get(sid)
        if gid and gid in GAME["games"]:
            game = GAME["games"][gid]
            game["last_seen"][sid] = time.time()
            return {"status": "matched", "game_id": gid, "symbol": game["players"][sid]}

        if GAME["waiting"] and GAME["waiting"] != sid:
            opponent = GAME["waiting"]
            GAME["waiting"] = None
            gid = uuid.uuid4().hex
            now = time.time()
            GAME["games"][gid] = {
                "board": [None] * 9,
                "turn": "X",
                "starter": "X",
                "players": {opponent: "X", sid: "O"},
                "winner": None,
                "score": {"X": 0, "O": 0},
                "last_seen": {opponent: now, sid: now},
            }
            GAME["player_game"][opponent] = gid
            GAME["player_game"][sid] = gid
            return {"status": "matched", "game_id": gid, "symbol": "O"}

        GAME["waiting"] = sid
        return {"status": "waiting"}


def game_state(sid, gid):
    with GAME_LOCK:
        game = GAME["games"].get(gid)
        if not game or sid not in game["players"]:
            return {"error": "not_found"}
        game["last_seen"][sid] = time.time()
        opponent = next(s for s in game["players"] if s != sid)
        opponent_connected = (time.time() - game["last_seen"].get(opponent, 0)) < OPPONENT_TIMEOUT
        return {
            "board": game["board"],
            "turn": game["turn"],
            "winner": game["winner"],
            "score": game["score"],
            "symbol": game["players"][sid],
            "opponent_connected": opponent_connected,
        }


def game_move(sid, gid, index):
    with GAME_LOCK:
        game = GAME["games"].get(gid)
        if not game or sid not in game["players"]:
            return {"error": "not_found"}
        symbol = game["players"][sid]
        if game["winner"] is not None or game["turn"] != symbol:
            return {"error": "not_your_turn"}
        if not isinstance(index, int) or not (0 <= index <= 8) or game["board"][index]:
            return {"error": "invalid_move"}
        game["board"][index] = symbol
        game["winner"] = check_winner(game["board"])
        if game["winner"] in ("X", "O"):
            game["score"][game["winner"]] += 1
        game["turn"] = "O" if symbol == "X" else "X"
        game["last_seen"][sid] = time.time()
        return {"ok": True}


def game_reset(sid, gid):
    with GAME_LOCK:
        game = GAME["games"].get(gid)
        if not game or sid not in game["players"]:
            return {"error": "not_found"}
        game["board"] = [None] * 9
        game["winner"] = None
        game["starter"] = "O" if game["starter"] == "X" else "X"
        game["turn"] = game["starter"]
        game["last_seen"][sid] = time.time()
        return {"ok": True}


def game_leave(sid):
    with GAME_LOCK:
        if GAME["waiting"] == sid:
            GAME["waiting"] = None
        gid = GAME["player_game"].pop(sid, None)
        if gid:
            _cleanup_stale_game(gid)
        return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _session_id(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" in cookie:
            return cookie["sid"].value, None
        new_sid = uuid.uuid4().hex
        return new_sid, new_sid

    def _send_json(self, payload, new_sid):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if new_sid:
            self.send_header("Set-Cookie", f"sid={new_sid}; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        sid, new_sid = self._session_id()
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/game/join":
            self._send_json(game_join(sid), new_sid)
            return

        if path == "/api/game/state":
            gid = query.get("game_id", [None])[0]
            self._send_json(game_state(sid, gid), new_sid)
            return

        self.path = path  # normalize for the legacy checks below

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

    def do_POST(self):
        sid, new_sid = self._session_id()
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}

        if path == "/api/game/move":
            self._send_json(game_move(sid, data.get("game_id"), data.get("index")), new_sid)
            return

        if path == "/api/game/reset":
            self._send_json(game_reset(sid, data.get("game_id")), new_sid)
            return

        if path == "/api/game/leave":
            self._send_json(game_leave(sid), new_sid)
            return

        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    port = 8765
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"One Mac, Live running at http://localhost:{port}")
    server.serve_forever()
