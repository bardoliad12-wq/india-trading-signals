#!/usr/bin/env python3
"""
Generate the EOD signals dashboard (docs/index.html) from watchlist.json using
yfinance. Runs on a GitHub Actions runner (real internet + volume + DMAs), so it
gives accurate breakout/stop signals. Signals-only: NO cost basis / P&L.

Sections:
  - Positions (stop watch): stop-hit + 50/200-DMA trend-break flags
  - Momentum (VCP): breakout (close > pivot on >=1.5x vol) / approaching pivot
  - CANSLIM leaders: new 52w-high break (>= trigger on volume) / approaching
"""
import json, os, html, datetime as dt, zoneinfo
import warnings; warnings.filterwarnings("ignore")
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
VOL_BREAKOUT = 1.5
APPROACH_PCT = 2.0
IST = zoneinfo.ZoneInfo("Asia/Kolkata")


def load():
    with open(os.path.join(HERE, "watchlist.json")) as f:
        return json.load(f)


def fetch(tickers):
    data = {}
    df = yf.download(tickers, period="1y", group_by="ticker",
                     auto_adjust=False, progress=False, threads=True)
    for t in tickers:
        try:
            sub = (df[t] if len(tickers) > 1 else df).dropna()
            if len(sub) >= 30:
                data[t] = sub
        except Exception:
            pass
    return data


def metrics(sub):
    c, v = sub["Close"], sub["Volume"]
    return {
        "last": float(c.iloc[-1]),
        "chg": (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100,
        "vol": float(v.iloc[-1]),
        "avgvol": float(v.tail(50).mean()),
        "hi52": float(sub["High"].max()),
        "dma50": float(c.tail(50).mean()),
        "dma200": float(c.tail(200).mean()) if len(c) >= 200 else None,
    }


def build(wl, data):
    alerts, pos, mom, cans = [], [], [], []

    for h in wl.get("positions_stopwatch", []):
        m = data.get(h["ticker"])
        if m is None:
            pos.append({"sym": h["symbol"], "cells": ["—", "no data", ""], "kind": "muted"}); continue
        m = metrics(m); stop = h.get("stop"); flags = []; kind = "ok"
        if stop and m["last"] <= stop:
            alerts.append(("stop", f"STOP HIT — {h['symbol']} ₹{m['last']:.1f} ≤ stop ₹{stop:.0f}: exit / review"))
            flags.append("STOP HIT"); kind = "stop"
        elif m["last"] < m["dma50"]:
            flags.append("below 50-DMA")
            if m["dma200"] and m["last"] < m["dma200"]:
                flags.append("below 200-DMA")
            kind = "warn"
        pos.append({"sym": h["symbol"],
                    "cells": [f"₹{m['last']:.1f}", f"{m['chg']:+.1f}%",
                              (f"stop ₹{stop:.0f}" if stop else "—") + ("  ·  " + ", ".join(flags) if flags else "")],
                    "kind": kind})

    for c in wl.get("momentum_vcp", []):
        m = data.get(c["ticker"])
        if m is None:
            mom.append({"sym": c["symbol"], "cells": ["—", "—", "no data"], "kind": "muted"}); continue
        m = metrics(m); piv = c["pivot"]; dist = (m["last"] / piv - 1) * 100
        volx = m["vol"] / m["avgvol"] if m["avgvol"] else 0; kind = "ok"; state = f"{dist:+.1f}% from pivot"
        if m["last"] > piv and volx >= VOL_BREAKOUT:
            alerts.append(("go", f"BREAKOUT — {c['symbol']} ₹{m['last']:.1f} &gt; pivot ₹{piv:.1f} on {volx:.1f}× vol: buy trigger"))
            state = f"BREAKOUT · {volx:.1f}× vol"; kind = "go"
        elif m["last"] > piv:
            alerts.append(("warn", f"above pivot on weak vol ({volx:.1f}×) — {c['symbol']} ₹{m['last']:.1f}: wait for volume"))
            state = f"above pivot · {volx:.1f}× vol (unconfirmed)"; kind = "warn"
        elif abs(dist) <= APPROACH_PCT:
            alerts.append(("near", f"APPROACHING — {c['symbol']} ₹{m['last']:.1f}, {dist:+.1f}% from pivot ₹{piv:.1f}"))
            kind = "near"
        mom.append({"sym": c["symbol"], "cells": [f"₹{m['last']:.1f}", f"pivot ₹{piv:.1f}", state], "kind": kind})

    for c in wl.get("canslim_leaders", []):
        m = data.get(c["ticker"])
        if m is None:
            cans.append({"sym": c["symbol"], "cells": ["—", "—", "no data"], "kind": "muted"}); continue
        m = metrics(m); trig = c["trigger"]; dist = (m["last"] / trig - 1) * 100
        volx = m["vol"] / m["avgvol"] if m["avgvol"] else 0; pcthi = m["last"] / m["hi52"] * 100
        kind = "ok"; state = f"{dist:+.1f}% from trigger · {pcthi:.0f}% of 52wH"
        if m["last"] >= trig and volx >= VOL_BREAKOUT:
            alerts.append(("go", f"NEW HIGH — {c['symbol']} ({c['theme']}) ₹{m['last']:.1f} &gt; ₹{trig:.1f} on {volx:.1f}× vol: buy trigger"))
            state = f"NEW 52w HIGH · {volx:.1f}× vol"; kind = "go"
        elif dist >= -APPROACH_PCT:
            alerts.append(("near", f"APPROACHING HIGH — {c['symbol']} ({c['theme']}) ₹{m['last']:.1f}, {dist:+.1f}% from ₹{trig:.1f}"))
            kind = "near"
        cans.append({"sym": f"{c['symbol']} · {c['theme']}",
                     "cells": [f"₹{m['last']:.1f}", f"trigger ₹{trig:.1f}", state], "kind": kind})

    return alerts, pos, mom, cans


CSS = """
:root{--bg:#f7f8fa;--card:#fff;--tx:#1c2024;--mut:#6b7280;--bd:#e5e7eb;
--go:#0a7d33;--gobg:#e7f6ec;--stop:#c0243c;--stopbg:#fdeaed;--warn:#a9600a;--warnbg:#fdf3e3;--near:#1f5fb8;--nearbg:#e9f1fc;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:760px;margin:0 auto;padding:18px 14px 60px}
h1{font-size:19px;margin:0 0 2px}.sub{color:var(--mut);font-size:13px;margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:14px 14px 6px;margin:0 0 16px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);margin:2px 0 10px}
.alert{display:flex;gap:9px;align-items:flex-start;padding:10px 12px;border-radius:10px;margin:0 0 8px;font-size:14px}
.alert .dot{width:8px;height:8px;border-radius:50%;margin-top:6px;flex:0 0 auto}
.go{background:var(--gobg)}.go .dot{background:var(--go)}
.stop{background:var(--stopbg)}.stop .dot{background:var(--stop)}
.warn{background:var(--warnbg)}.warn .dot{background:var(--warn)}
.near{background:var(--nearbg)}.near .dot{background:var(--near)}
.none{color:var(--mut);font-size:14px;padding:6px 2px}
table{width:100%;border-collapse:collapse;font-size:14px}
td{padding:9px 6px;border-top:1px solid var(--bd);vertical-align:top}
tr:first-child td{border-top:none}
.sym{font-weight:600;white-space:nowrap}.px{white-space:nowrap;color:var(--mut)}
.st-go{color:var(--go);font-weight:600}.st-stop{color:var(--stop);font-weight:600}
.st-warn{color:var(--warn)}.st-near{color:var(--near);font-weight:600}.st-muted{color:var(--mut)}
.foot{color:var(--mut);font-size:12px;margin-top:22px;line-height:1.6}
@media(prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#181c22;--tx:#e6e8eb;--mut:#9aa4b2;--bd:#2a2f37;
--gobg:#0e2a18;--stopbg:#2e1116;--warnbg:#2a1f0c;--nearbg:#101f33;}}
"""


def render(alerts, pos, mom, cans):
    now = dt.datetime.now(IST)
    kmap = {"go": "st-go", "stop": "st-stop", "warn": "st-warn", "near": "st-near", "ok": "", "muted": "st-muted"}

    def table(rows):
        out = ["<table>"]
        for r in rows:
            c = r["cells"]; sc = kmap.get(r["kind"], "")
            out.append(f'<tr><td class="sym">{html.escape(r["sym"])}</td>'
                       f'<td class="px">{c[0]}</td><td class="px">{c[1]}</td>'
                       f'<td class="{sc}">{c[2]}</td></tr>')
        out.append("</table>")
        return "".join(out)

    if alerts:
        order = {"stop": 0, "go": 1, "warn": 2, "near": 3}
        alerts = sorted(alerts, key=lambda a: order.get(a[0], 9))
        albox = "".join(f'<div class="alert {k}"><span class="dot"></span><div>{msg}</div></div>' for k, msg in alerts)
    else:
        albox = '<div class="none">No actionable triggers today.</div>'

    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="1800">
<title>EOD Signals — {now:%d %b %Y}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>India-Trading — EOD Signals</h1>
<p class="sub">Updated {now:%a %d %b %Y, %H:%M IST} · auto-refreshes · signals only, verify volume &amp; price on your broker before acting</p>
<div class="card"><h2>Actionable</h2>{albox}</div>
<div class="card"><h2>Positions · stop watch</h2>{table(pos)}</div>
<div class="card"><h2>Momentum · VCP pivots</h2>{table(mom)}</div>
<div class="card"><h2>CANSLIM leaders · 52w-high breaks</h2>{table(cans)}</div>
<p class="foot">Breakout = close above pivot on ≥1.5× average volume. Pivot/trigger/stop levels are static (from the last screen) — refresh periodically.<br>
Not investment advice. Educational signals from public price data; patterns fail — always use a stop.</p>
</div></body></html>"""


def main():
    wl = load()
    tickers = [x["ticker"] for g in ("positions_stopwatch", "momentum_vcp", "canslim_leaders")
               for x in wl.get(g, [])]
    data = fetch(tickers)
    alerts, pos, mom, cans = build(wl, data)
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(render(alerts, pos, mom, cans))

    hist_path = os.path.join(DOCS, "history.json")
    hist = []
    if os.path.exists(hist_path):
        try: hist = json.load(open(hist_path))
        except Exception: hist = []
    hist = [h for h in hist if h.get("date") != dt.date.today().isoformat()]
    hist.append({"date": dt.date.today().isoformat(),
                 "fetched": len(data), "of": len(tickers),
                 "alerts": [m for _, m in alerts]})
    json.dump(hist[-120:], open(hist_path, "w"), indent=1)
    print(f"OK: fetched {len(data)}/{len(tickers)} tickers, {len(alerts)} alerts -> docs/index.html")


if __name__ == "__main__":
    main()
