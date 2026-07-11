#!/usr/bin/env python3
"""
EOD signals dashboard (docs/index.html) from watchlist.json via yfinance.
Runs on a GitHub Actions runner (real internet + volume + DMAs). Signals-only:
NO cost basis / entry price / P&L is stored or shown.

BUY signals (watch items, no stop set): breakout (close > pivot on >=1.5x vol) / approaching.
SELL signals (holdings, or vcp/canslim items with a numeric `stop`), all PRICE-based:
  - close <= stop            -> STOP HIT (sell)
  - close < 50-DMA           -> TREND EXIT (reduce/exit)
Soft warning (not a sell): down day (<= -1%) on > 1.5x avg volume -> DISTRIBUTION.
"""
import json, os, html, datetime as dt, zoneinfo
import warnings; warnings.filterwarnings("ignore")
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
VOL_BREAKOUT = 1.5
APPROACH_PCT = 2.0
DIST_CHG = -1.0      # a down day of at least this % ...
DIST_VOL = 1.5       # ... on at least this x avg volume => distribution warning
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


def distribution(m):
    volx = m["vol"] / m["avgvol"] if m["avgvol"] else 0
    return m["chg"] <= DIST_CHG and volx >= DIST_VOL, volx


def held_signals(sym, m, stop, note=""):
    """Return (row_dict, alerts) for a position we hold -> sell/exit rules."""
    alerts = []
    dist, volx = distribution(m)
    tag = f" · {note}" if note else ""
    if stop and m["last"] <= stop:
        state = f"STOP HIT — close ₹{m['last']:.1f} ≤ stop ₹{stop:.0f}"
        kind = "sell"
        alerts.append(("sell", f"SELL — {sym} STOP HIT ₹{m['last']:.1f} ≤ ₹{stop:.0f}: exit / review"))
    elif m["last"] < m["dma50"]:
        below200 = " & 200-DMA" if m["dma200"] and m["last"] < m["dma200"] else ""
        state = f"TREND EXIT — closed below 50-DMA ₹{m['dma50']:.1f}{below200}"
        kind = "sell"
        alerts.append(("sell", f"SELL — {sym} closed below 50-DMA (₹{m['dma50']:.1f}){below200}: reduce / exit"))
    else:
        state = f"holding · above 50-DMA ₹{m['dma50']:.1f}"
        if stop:
            state += f" · stop ₹{stop:.0f}"
        kind = "ok"
    if dist:
        state += "  ⚠ distribution"
        alerts.append(("warn", f"⚠ {sym} distribution — {m['chg']:+.1f}% on {volx:.1f}× vol: watch for a top"))
    return {"sym": sym + tag, "px": m["last"], "chg": m["chg"], "state": state, "kind": kind}, alerts


def buy_signals(sym, m, level, label, theme=""):
    """Return (row_dict, alerts) for a watch item -> buy rules. label = 'pivot'|'trigger'."""
    alerts = []
    name = f"{sym} · {theme}" if theme else sym
    dist = (m["last"] / level - 1) * 100
    volx = m["vol"] / m["avgvol"] if m["avgvol"] else 0
    sstop = round(level * 0.92)   # suggested initial stop ~8% below the breakout level
    if m["last"] > level and volx >= VOL_BREAKOUT:
        word = "BREAKOUT" if label == "pivot" else "NEW HIGH"
        state = f"{word} · {volx:.1f}× vol · if bought, stop ≈ ₹{sstop}"
        kind = "go"
        alerts.append(("go", f"BUY — {sym} {word} ₹{m['last']:.1f} &gt; {label} ₹{level:.1f} on {volx:.1f}× vol · initial stop ≈ ₹{sstop}"))
    elif m["last"] > level:
        state = f"above {label} on weak vol ({volx:.1f}×) — wait for volume"
        kind = "warn"
        alerts.append(("warn", f"{sym} above {label} on weak vol ({volx:.1f}×): unconfirmed, wait"))
    elif abs(dist) <= APPROACH_PCT:
        state = f"approaching · {dist:+.1f}% from {label} ₹{level:.1f}"
        kind = "near"
        alerts.append(("near", f"APPROACHING — {sym} ₹{m['last']:.1f}, {dist:+.1f}% from {label} ₹{level:.1f}"))
    else:
        state = f"{dist:+.1f}% from {label} ₹{level:.1f}"
        kind = "ok"
    return {"sym": name, "px": m["last"], "chg": m["chg"], "state": state, "kind": kind}, alerts


def build(wl, data):
    alerts, hold_rows, mom_rows, cans_rows = [], [], [], []

    def norow(sym):
        return {"sym": sym, "px": None, "chg": None, "state": "no data", "kind": "muted"}

    for h in wl.get("holdings", []):
        m = data.get(h["ticker"])
        if m is None:
            hold_rows.append(norow(h["symbol"])); continue
        row, al = held_signals(h["symbol"], metrics(m), h.get("stop"), h.get("note", ""))
        hold_rows.append(row); alerts += al

    for c in wl.get("momentum_vcp", []):
        m = data.get(c["ticker"])
        if m is None:
            mom_rows.append(norow(c["symbol"])); continue
        mm = metrics(m)
        if c.get("stop"):   # entered -> manage the exit
            row, al = held_signals(c["symbol"], mm, c.get("stop"))
        else:               # still on watch -> buy rules
            row, al = buy_signals(c["symbol"], mm, c["pivot"], "pivot")
        mom_rows.append(row); alerts += al

    for c in wl.get("canslim_leaders", []):
        m = data.get(c["ticker"])
        if m is None:
            cans_rows.append(norow(c["symbol"])); continue
        mm = metrics(m)
        if c.get("stop"):
            row, al = held_signals(c["symbol"] + " · " + c.get("theme", ""), mm, c.get("stop"))
        else:
            row, al = buy_signals(c["symbol"], mm, c["trigger"], "trigger", c.get("theme", ""))
        cans_rows.append(row); alerts += al

    return alerts, hold_rows, mom_rows, cans_rows


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--tx:#161a1d;--mut:#6b7280;--bd:#e6e8ec;
--go:#0a7d33;--gobg:#e7f6ec;--sell:#c0243c;--sellbg:#fdeaed;--warn:#9a5b00;--warnbg:#fdf2e2;--near:#1f5fb8;--nearbg:#e9f1fc;}
@media(prefers-color-scheme:dark){:root{--bg:#0e1114;--card:#171b20;--tx:#e7e9ec;--mut:#98a2b0;--bd:#282e36;
--gobg:#0e2a18;--sellbg:#2e1116;--warnbg:#2a1f0c;--nearbg:#101f33;}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:640px;margin:0 auto;padding:16px 12px 64px}
h1{font-size:20px;margin:0 0 2px}.sub{color:var(--mut);font-size:13px;margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:16px;overflow:hidden;margin:0 0 14px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0;padding:13px 14px 9px}
/* alerts */
.alerts{padding:0 12px 6px}
.alert{display:flex;gap:10px;align-items:flex-start;padding:12px 12px;border-radius:12px;margin:0 0 8px;font-size:14.5px;line-height:1.45}
.alert .dot{width:9px;height:9px;border-radius:50%;margin-top:6px;flex:0 0 auto}
.a-go{background:var(--gobg)}.a-go .dot{background:var(--go)}
.a-sell{background:var(--sellbg)}.a-sell .dot{background:var(--sell)}
.a-warn{background:var(--warnbg)}.a-warn .dot{background:var(--warn)}
.a-near{background:var(--nearbg)}.a-near .dot{background:var(--near)}
.none{color:var(--mut);font-size:14.5px;padding:4px 14px 14px}
/* rows: stacked, mobile-first, left accent */
.row{padding:12px 14px;border-top:1px solid var(--bd);border-left:3px solid var(--bd)}
.row:first-of-type{border-top:none}
.r1{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.sym{font-weight:650;font-size:15.5px}
.pr{font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--mut);font-size:14px}
.chg{font-size:12.5px;margin-left:6px}.up{color:var(--go)}.down{color:var(--sell)}
.sig{font-size:13.5px;color:var(--mut);margin-top:3px;line-height:1.45}
.k-go{border-left-color:var(--go)}.k-go .sig{color:var(--go);font-weight:600}
.k-sell{border-left-color:var(--sell)}.k-sell .sig{color:var(--sell);font-weight:600}
.k-warn{border-left-color:var(--warn)}.k-warn .sig{color:var(--warn)}
.k-near{border-left-color:var(--near)}.k-near .sig{color:var(--near);font-weight:600}
.k-muted .sig{opacity:.7}
.foot{color:var(--mut);font-size:12px;margin-top:20px;line-height:1.6}
.foot b{color:var(--tx);font-weight:600}
/* watchlist editor accordion */
.acc summary{cursor:pointer;list-style:none;padding:13px 14px;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);font-weight:600;display:flex;justify-content:space-between;align-items:center}
.acc summary::-webkit-details-marker{display:none}
.acc summary .chev{transition:transform .2s}.acc[open] summary .chev{transform:rotate(180deg)}
.ebody{padding:0 14px 14px}
.enote{font-size:12.5px;color:var(--mut);margin:0 0 12px;line-height:1.5}
.egrp{margin:0 0 14px}
.eglabel{font-size:12px;font-weight:650;margin:0 0 8px;color:var(--tx)}
.eitem{border:1px solid var(--bd);border-radius:12px;padding:9px 10px;margin:0 0 8px;display:grid;grid-template-columns:1fr 1fr;gap:8px}
.efield{display:flex;flex-direction:column;gap:3px;font-size:11px;color:var(--mut);min-width:0}
.efield span{text-transform:uppercase;letter-spacing:.03em}
.efield input{font-size:16px;padding:8px 9px;border:1px solid var(--bd);border-radius:8px;background:var(--bg);color:var(--tx);width:100%}
.ebtns{display:flex;gap:8px;flex-wrap:wrap;margin-top:2px}
.ebtns button,.ebtns a{font-size:14px;padding:10px 14px;border-radius:10px;border:1px solid var(--bd);background:var(--card);color:var(--tx);font-weight:600;cursor:pointer;text-decoration:none;display:inline-block}
.ebtns button.primary{background:var(--tx);color:var(--bg);border-color:var(--tx)}
.emsg{font-size:12.5px;color:var(--go);margin-top:8px;min-height:16px}
"""


GH_EDIT_URL = "https://github.com/bardoliad12-wq/india-trading-signals/edit/main/watchlist.json"

EDITOR_JS = """
function wlBuild(){
  var wl = JSON.parse(document.getElementById('wl-data').textContent);
  var inp = document.querySelectorAll('#wl-editor [data-g]');
  for (var k=0;k<inp.length;k++){
    var el=inp[k], g=el.getAttribute('data-g'), i=+el.getAttribute('data-i'),
        f=el.getAttribute('data-f'), t=el.getAttribute('data-t'), v=el.value.trim(), val;
    if(t==='num'){ val = (v==='') ? null : (isNaN(parseFloat(v)) ? v : parseFloat(v)); }
    else { val = v; }
    wl[g][i][f]=val;
  }
  return JSON.stringify(wl, null, 2);
}
function wlMsg(m){ document.getElementById('wlmsg').textContent = m; }
function wlCopy(){ var s=wlBuild();
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(s).then(function(){wlMsg('Copied. Paste into watchlist.json on GitHub and commit to apply.');},
                                          function(){wlMsg('Copy blocked by browser — use Download instead.');});
  } else { wlMsg('Clipboard unsupported — use Download.'); } }
function wlDownload(){ var s=wlBuild(); var b=new Blob([s],{type:'application/json'});
  var a=document.createElement('a'); a.href=URL.createObjectURL(b); a.download='watchlist.json'; a.click();
  wlMsg('Downloaded watchlist.json — commit it on GitHub to apply.'); }
"""


def editor(wl):
    groups = [("holdings", "Holdings"), ("momentum_vcp", "Momentum · VCP"),
              ("canslim_leaders", "CANSLIM leaders")]
    out = []
    for g, label in groups:
        out.append(f'<div class="egrp"><div class="eglabel">{html.escape(label)}</div>')
        for i, item in enumerate(wl.get(g, [])):
            out.append('<div class="eitem">')
            for f, v in item.items():
                if f.startswith("_"):
                    continue
                is_num = v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))
                t = "num" if is_num else "str"
                val = "" if v is None else html.escape(str(v), quote=True)
                out.append(
                    f'<label class="efield"><span>{html.escape(f)}</span>'
                    f'<input data-g="{g}" data-i="{i}" data-f="{html.escape(f, quote=True)}" '
                    f'data-t="{t}" inputmode="{"decimal" if is_num else "text"}" value="{val}"></label>')
            out.append('</div>')
        out.append('</div>')
    return "".join(out)


def accordion(wl):
    body = (
        '<p class="enote">Edit levels/stops below, then <b>Copy</b> or <b>Download</b> the updated '
        '<code>watchlist.json</code> and commit it on GitHub — a static page can\'t save changes itself. '
        'Add a <code>stop</code> to a VCP/CANSLIM row to flip it into sell mode.</p>'
        + editor(wl)
        + '<div class="ebtns"><button type="button" class="primary" onclick="wlCopy()">Copy JSON</button>'
          '<button type="button" onclick="wlDownload()">Download</button>'
          f'<a href="{GH_EDIT_URL}" target="_blank" rel="noopener">Edit on GitHub ↗</a></div>'
          '<div class="emsg" id="wlmsg"></div>')
    acc = ('<details class="card acc" id="wl-editor"><summary>⚙ Watchlist editor'
           '<span class="chev">⌄</span></summary><div class="ebody">' + body + '</div></details>')
    wl_json = json.dumps(wl, ensure_ascii=False, indent=2).replace("</", "<\\/")
    scripts = (f'<script id="wl-data" type="application/json">{wl_json}</script>'
               f'<script>{EDITOR_JS}</script>')
    return acc, scripts


def render(alerts, hold, mom, cans, wl):
    now = dt.datetime.now(IST)
    akmap = {"go": "a-go", "sell": "a-sell", "warn": "a-warn", "near": "a-near"}
    acc, scripts = accordion(wl)

    def rows_html(rows):
        out = []
        for r in rows:
            px = "—" if r["px"] is None else f"₹{r['px']:.1f}"
            chg = ""
            if r["chg"] is not None:
                cls = "up" if r["chg"] >= 0 else "down"
                chg = f'<span class="chg {cls}">{r["chg"]:+.1f}%</span>'
            out.append(
                f'<div class="row k-{r["kind"]}"><div class="r1">'
                f'<span class="sym">{html.escape(r["sym"])}</span>'
                f'<span class="pr">{px}{chg}</span></div>'
                f'<div class="sig">{r["state"]}</div></div>')
        return "".join(out)

    if alerts:
        order = {"sell": 0, "go": 1, "warn": 2, "near": 3}
        alerts = sorted(alerts, key=lambda a: order.get(a[0], 9))
        albox = "".join(
            f'<div class="alert {akmap.get(k,"a-near")}"><span class="dot"></span><div>{msg}</div></div>'
            for k, msg in alerts)
        albox = f'<div class="alerts">{albox}</div>'
    else:
        albox = '<div class="none">No actionable triggers today.</div>'

    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><meta http-equiv="refresh" content="1800">
<title>EOD Signals — {now:%d %b %Y}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>India-Trading — EOD Signals</h1>
<p class="sub">Updated {now:%a %d %b %Y, %H:%M IST} · auto-refreshes · signals only — verify price &amp; volume on your broker before acting</p>
<div class="card"><h2>Actionable</h2>{albox}</div>
<div class="card"><h2>Holdings · sell watch</h2>{rows_html(hold)}</div>
<div class="card"><h2>Momentum · VCP</h2>{rows_html(mom)}</div>
<div class="card"><h2>CANSLIM leaders</h2>{rows_html(cans)}</div>
{acc}
<p class="foot">
<b>Buy</b> = close above pivot/trigger on ≥1.5× avg volume (breakout shows a suggested initial stop ≈8% below).<br>
<b>Sell (price-based):</b> close ≤ your stop, or close below the 50-DMA (trend exit — trails winners up). ⚠ <b>distribution</b> = a down day on &gt;1.5× volume — a warning, not a sell.<br>
Add a numeric <code>stop</code> to a VCP/CANSLIM item in watchlist.json to switch it from buy-watch to sell mode.<br>
Not investment advice. Educational signals from public price data; patterns fail — always use a stop.</p>
</div>{scripts}</body></html>"""


def main():
    wl = load()
    tickers = [x["ticker"] for g in ("holdings", "momentum_vcp", "canslim_leaders")
               for x in wl.get(g, [])]
    data = fetch(tickers)
    alerts, hold, mom, cans = build(wl, data)
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(render(alerts, hold, mom, cans, wl))

    hp = os.path.join(DOCS, "history.json")
    hist = []
    if os.path.exists(hp):
        try: hist = json.load(open(hp))
        except Exception: hist = []
    hist = [h for h in hist if h.get("date") != dt.date.today().isoformat()]
    hist.append({"date": dt.date.today().isoformat(), "fetched": len(data),
                 "of": len(tickers), "alerts": [m for _, m in alerts]})
    json.dump(hist[-120:], open(hp, "w"), indent=1)
    print(f"OK: {len(data)}/{len(tickers)} tickers, {len(alerts)} alerts -> docs/index.html")


if __name__ == "__main__":
    main()
