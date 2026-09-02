# 🖥️ One Mac, Live

### Web Based System Monitoring Dashboard
**That tracks & broadcasts the real time performance metrics of an active computer.**

*Reporting what Rahul Shaw's MacBook Air is doing right now.*

> *Somewhere on a desk, a MacBook Air is breathing — and you can watch it happen.*

**One Mac, Live** is a real-time vitals monitor for a single machine (mine). No cloud dashboards, no telemetry pipelines, no accounts — just raw stats pulled straight from macOS every second and pushed to a page that anyone can open and stare at like an aquarium.

CPU spiking? You'll see it. Fans about to spin up because Chrome ate the RAM again? You'll see that too. It's system monitoring as a spectator sport.

---

## 👀 What you're looking at

Open the page and you get a live readout of:

| Card | What it shows |
|---|---|
| **Processor load** | Current load % + 1/5/15-minute load averages |
| **Memory** | Used vs. total RAM |
| **Disk** | Used vs. total storage on `/` |
| **Network** | Live download/upload throughput (KB/s) |
| **Uptime** | How long this Mac has been awake |
| **Hardware** | Chip, core count, installed RAM |

...plus a little social layer: a visitor counter tells you your place in line (*"You are visitor #47"*) and how many other people are watching **right now**, live, alongside you.

Everything refreshes every second. No page reloads, no polling libraries — just `fetch()` on a loop.

---

## ⚙️ How it works

It's deliberately, almost stubbornly simple:

```
┌────────────────────┐        GET /api/stats every 1s        ┌──────────────────────┐
│   index.html        │ ─────────────────────────────────────▶ │   server.py           │
│   (vanilla JS/CSS)  │ ◀───────────────────────────────────── │   (Python stdlib)     │
└────────────────────┘              JSON payload               └──────────────────────┘
                                                                          │
                                                                          ▼
                                                          sysctl · vm_stat · df · netstat
                                                             (shelling out to macOS)
```

- **`server.py`** — a `ThreadingHTTPServer` built entirely on Python's standard library. No Flask, no FastAPI, no `pip install` anything. It shells out to native macOS tools (`sysctl`, `vm_stat`, `df`, `netstat`) to read real hardware/OS state, parses the output, and serves it as JSON at `/api/stats`.
- **`index.html`** — a single self-contained page (styles, markup, and script all inline) that polls `/api/stats` once a second and updates the DOM. No frameworks, no build step, no `node_modules`.
- **Visitor tracking** — a lightweight cookie-based session system counts concurrent viewers and total visitors in memory. Sessions expire after 5 seconds of inactivity, so "watching live" actually means *live*.

That's the whole stack. It's the kind of project that fits in your head in one sitting.

---

## 🚀 Running it yourself

```bash
git clone https://github.com/rahulshaw-pm/one-mac-live.git
cd one-mac-live
python3 server.py
```

Then open **http://localhost:8765** and watch your own machine think.

**Requirements:** macOS (it leans on `sysctl`/`vm_stat`/`netstat`, which are Darwin-specific) and Python 3 — nothing else. No `requirements.txt` because there are no dependencies.

---

## 📡 API

Prefer raw numbers to pretty cards? Hit the endpoint directly:

```
GET /api/stats
```

```json
{
  "cpu": { "load1": 2.1, "load5": 1.8, "load15": 1.5, "load_pct": 26.3 },
  "memory": { "used_pct": 61.2, "used_gb": 10.4, "total_gb": 17.0 },
  "disk": { "used_pct": 48.9, "used_gb": 245.1, "total_gb": 500.0 },
  "network": { "rx_kbps": 132.4, "tx_kbps": 18.7 },
  "uptime_seconds": 302145,
  "hardware": { "chip": "Apple M2", "cores": 8, "ram_gb": 16 },
  "visitor": { "your_number": 47, "concurrent": 3, "total": 812 }
}
```

---

## 🤔 Why does this exist?

Because "my computer is fine" is a claim, not a proof. This is the proof — updated every second, for anyone curious enough to check.

---

## 📄 License

Personal project. Peek responsibly.
