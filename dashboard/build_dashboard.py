#!/usr/bin/env python3
"""Build the second-brain dashboard as a self-contained static index.html.

Pulls everything from dashboard/collect.py (todos, scheduled agents, GHL, goals)
and bakes a dark "command center" page — no server, no fetch. Works double-clicked
on the Mac and opened from Files on iPhone.

Output: dashboard/index.html  +  <iCloud Drive>/SecondBrain/index.html

Run:  uv run python dashboard/build_dashboard.py
Flags: --no-ghl   skip the live GHL call (faster offline builds)
"""
from __future__ import annotations

import html
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from store_lib import LOCAL_TZ, _flock  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect import collect_all  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOCAL_OUT = ROOT / "dashboard" / "index.html"
ICLOUD_OUT = (
    Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    / "SecondBrain" / "index.html"
)
PRIO = {1: "high", 2: "normal", 3: "low", None: ""}


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def fmt_time(iso) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%-I:%M %p")
    except (ValueError, TypeError):
        return ""


def fmt_day(iso) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%a %-I:%M %p")
    except (ValueError, TypeError):
        return ""


def todo_li(t: dict, day=False) -> str:
    when = fmt_day(t["scheduled_time"]) if day else fmt_time(t.get("scheduled_time"))
    proj = t.get("project") or ""
    p = t.get("priority")
    return (
        f'<li class="todo p{esc(p) if p else "0"}">'
        f'<span class="dot"></span>'
        f'<span class="txt">{esc(t.get("text"))}</span>'
        f'<span class="rt">{f"<span class=tag>{esc(proj)}</span>" if proj else ""}'
        f'{f"<span class=when>{esc(when)}</span>" if when else ""}</span></li>'
    )


def list_section(title, items, empty, day=False) -> str:
    body = "".join(todo_li(t, day) for t in items) if items else f'<li class="empty">{esc(empty)}</li>'
    return (
        f'<section class="card"><h2>{esc(title)}<i>{len(items)}</i></h2>'
        f'<ul class="todos">{body}</ul></section>'
    )


def goals_section(goals) -> str:
    if not goals:
        return ""
    rows = ""
    for g in goals:
        cur, tgt = g.get("current", 0), g.get("target", 0) or 1
        pct = max(0, min(100, round(100 * cur / tgt)))
        unit = g.get("unit", "")
        val = (f"{unit}{cur:,} / {unit}{tgt:,}" if unit == "$" else f"{cur:,} / {tgt:,}{unit}")
        rows += (
            f'<div class="goal"><div class="glabel"><span>{esc(g.get("label"))}</span>'
            f'<span class="gval">{esc(val)}</span></div>'
            f'<div class="bar"><div class="fill" style="width:{pct}%"></div></div></div>'
        )
    return f'<section class="card"><h2>Goals</h2>{rows}</section>'


def systems_section(agents, ghl) -> str:
    cards = ""
    if ghl.get("ok"):
        n = ghl.get("contacts")
        sub = f'{n:,} contacts' if n is not None else "connected"
        cards += f'<div class="sys ok"><span class="sdot"></span><b>GoHighLevel</b><span class="ssub">{esc(sub)}</span></div>'
    else:
        cards += f'<div class="sys off"><span class="sdot"></span><b>GoHighLevel</b><span class="ssub">{esc(ghl.get("msg","offline"))}</span></div>'
    for a in agents:
        cad = f' · {esc(a["cadence"])}' if a.get("cadence") else ""
        cards += (
            f'<div class="sys ok"><span class="sdot"></span>'
            f'<b>{esc(a["name"])}</b><span class="ssub">{esc(a.get("desc",""))[:70]}{cad}</span></div>'
        )
    return f'<section class="card"><h2>Systems</h2><div class="sysgrid">{cards}</div></section>'


def schengen_section(s) -> str:
    if not s or not s.get("ok"):
        return ""
    used = s.get("used", 0)
    limit = s.get("limit", 90)
    remaining = s.get("remaining", limit - used)
    pct = max(0, min(100, round(100 * used / limit)))
    mmd = s.get("max_more_days")
    state = "ok"
    if used >= 80 or (mmd is not None and mmd <= 14):
        state = "hot"
    elif used >= 70 or (mmd is not None and mmd <= 30):
        state = "warn"
    fill = {
        "warn": "background:linear-gradient(90deg,#b8800f,var(--warn));box-shadow:0 0 12px rgba(255,180,84,.5)",
        "hot": "background:linear-gradient(90deg,#b3263a,var(--hot));box-shadow:0 0 12px rgba(255,93,115,.5)",
    }.get(state, "")
    if s.get("currently_in_schengen"):
        leave = s.get("must_leave_by")
        sub = (f'in Schengen · exit by {esc(leave)}' if leave else "in Schengen now")
    else:
        sub = "outside Schengen"
    note = f' · {esc(mmd)} days of stay left' if (s.get("currently_in_schengen") and mmd is not None) else ""
    return (
        f'<section class="card"><h2>Schengen 90 / 180<i>{esc(used)}/{esc(limit)}</i></h2>'
        f'<div class="goal"><div class="glabel"><span>{sub}{note}</span>'
        f'<span class="gval">{esc(remaining)} days left</span></div>'
        f'<div class="bar"><div class="fill" style="width:{pct}%;{fill}"></div></div></div></section>'
    )


def build_html(data: dict) -> str:
    tb = data["todos"]
    counters = [
        ("Today", len(tb["today"])),
        ("Inbox", len(tb["inbox"])),
        ("Upcoming", len(tb["upcoming"])),
        ("Done today", len(tb["done_today"])),
    ]
    cblocks = "".join(
        f'<div class="counter"><b>{v}</b><span>{esc(k)}</span></div>' for k, v in counters
    )
    return f"""<!doctype html><html lang=en><head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta http-equiv=refresh content="600">
<title>Second Brain</title>
<style>
  :root{{color-scheme:dark;--bg:#070a10;--panel:#0d1219;--line:#18202c;--ink:#e7ecf4;
    --mut:#6c7891;--cy:#39e6ff;--cy2:#1b9fff;--hot:#ff5d73;--warn:#ffb454;--ok:#46e0a0}}
  *{{box-sizing:border-box}}
  body{{margin:0;padding:18px 14px 60px;background:
      radial-gradient(1200px 500px at 80% -10%,rgba(27,159,255,.10),transparent),
      radial-gradient(900px 500px at -10% 10%,rgba(57,230,255,.06),transparent),var(--bg);
    color:var(--ink);font:15px/1.45 -apple-system,system-ui,Segoe UI,sans-serif;
    max-width:980px;margin-inline:auto;-webkit-font-smoothing:antialiased}}
  header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}}
  h1{{margin:0;font-size:18px;letter-spacing:.5px;display:flex;align-items:center;gap:9px}}
  h1 .glow{{width:9px;height:9px;border-radius:50%;background:var(--cy);box-shadow:0 0 12px 2px var(--cy)}}
  .stamp{{font:11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--mut);
    border:1px solid var(--line);padding:6px 9px;border-radius:8px}}
  .counters{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}}
  .counter{{background:linear-gradient(180deg,rgba(57,230,255,.05),transparent),var(--panel);
    border:1px solid var(--line);border-radius:14px;padding:14px 10px;text-align:center}}
  .counter b{{display:block;font-size:30px;font-weight:700;color:#fff;letter-spacing:.5px;
    text-shadow:0 0 18px rgba(57,230,255,.35)}}
  .counter span{{font:10px/1 ui-monospace,monospace;color:var(--mut);text-transform:uppercase;letter-spacing:1.4px}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
  .grid .full{{grid-column:1/-1}}
  .card{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:15px 16px;margin-bottom:14px}}
  h2{{margin:0 0 11px;font:11px/1 ui-monospace,monospace;letter-spacing:2px;text-transform:uppercase;
    color:var(--cy);display:flex;align-items:center;gap:8px}}
  h2 i{{font-style:normal;color:var(--mut);background:#10161f;border:1px solid var(--line);
    border-radius:20px;padding:2px 8px;font-size:10px}}
  ul.todos{{list-style:none;margin:0;padding:0}}
  li.todo{{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid #131a24}}
  li.todo:first-child{{border-top:none}}
  .dot{{width:7px;height:7px;border-radius:50%;background:#33415a;flex:none}}
  .p1 .dot{{background:var(--hot);box-shadow:0 0 9px var(--hot)}}
  .p2 .dot{{background:var(--warn)}} .p3 .dot{{background:#3b82f6}}
  .p1 .txt{{font-weight:600}}
  .txt{{flex:1}} .rt{{display:flex;align-items:center;gap:7px;flex:none}}
  .tag{{font:10px/1 ui-monospace,monospace;color:#7fd4ff;background:rgba(27,159,255,.12);
    border:1px solid rgba(57,230,255,.2);padding:3px 7px;border-radius:6px}}
  .when{{font:11px/1 ui-monospace,monospace;color:var(--cy);font-variant-numeric:tabular-nums}}
  .empty{{color:var(--mut);font-style:italic;padding:6px 0}}
  .goal{{margin:11px 0}} .goal:first-child{{margin-top:2px}}
  .glabel{{display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px}}
  .gval{{font:11px/1 ui-monospace,monospace;color:var(--mut)}}
  .bar{{height:7px;background:#10161f;border-radius:6px;overflow:hidden}}
  .fill{{height:100%;background:linear-gradient(90deg,var(--cy2),var(--cy));
    box-shadow:0 0 12px rgba(57,230,255,.5);border-radius:6px}}
  .sysgrid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
  .sys{{display:flex;flex-direction:column;gap:3px;background:#0b1017;border:1px solid var(--line);
    border-radius:11px;padding:11px 12px;position:relative}}
  .sys b{{font-size:13px;font-weight:600}} .ssub{{font:11px/1.3 ui-monospace,monospace;color:var(--mut)}}
  .sdot{{position:absolute;top:12px;right:12px;width:8px;height:8px;border-radius:50%}}
  .sys.ok .sdot{{background:var(--ok);box-shadow:0 0 9px var(--ok)}}
  .sys.off .sdot{{background:var(--hot);box-shadow:0 0 9px var(--hot)}}
  .sys.off b{{color:#c9d2e0}}
  footer{{text-align:center;color:#3f4860;font:10px/1.4 ui-monospace,monospace;margin-top:22px;letter-spacing:.5px}}
  @media(max-width:640px){{.grid{{grid-template-columns:1fr}}.counters{{grid-template-columns:repeat(2,1fr)}}.sysgrid{{grid-template-columns:1fr}}}}
</style></head><body>
  <header>
    <h1><span class=glow></span>SECOND BRAIN</h1>
    <span class=stamp>{esc(data["generated"])}</span>
  </header>
  <div class=counters>{cblocks}</div>
  <div class=grid>
    {f'<div class=full>{schengen_section(data["schengen"])}</div>' if data.get("schengen", {}).get("ok") else ""}
    <div class=full>{list_section("Today", tb["today"], "Nothing scheduled today. Say: Hey Siri, remind me to…")}</div>
    {list_section("Inbox — needs triage", tb["inbox"], "Inbox clear.")}
    {list_section("Upcoming · 7 days", tb["upcoming"], "Nothing on deck.", day=True)}
    <div class=full>{goals_section(data["goals"])}</div>
    <div class=full>{systems_section(data["agents"], data["ghl"])}</div>
  </div>
  <footer>second brain · capture → schedule → execute · auto-refresh 10m</footer>
</body></html>"""


def main() -> int:
    include_ghl = "--no-ghl" not in sys.argv
    data = collect_all(include_ghl=include_ghl)
    out = build_html(data)
    LOCAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    # run.sh (every 10 min) and morning.sh both invoke this; lock + atomic-replace so an
    # overlapping run can't hand [OWNER] a half-written index.html (the file he opens directly).
    with _flock(LOCAL_OUT):
        tmp = LOCAL_OUT.with_suffix(LOCAL_OUT.suffix + ".tmp")
        tmp.write_text(out, encoding="utf-8")
        tmp.replace(LOCAL_OUT)
        print(f"Wrote {LOCAL_OUT}")
        try:
            ICLOUD_OUT.parent.mkdir(parents=True, exist_ok=True)
            itmp = ICLOUD_OUT.with_suffix(ICLOUD_OUT.suffix + ".tmp")
            itmp.write_text(out, encoding="utf-8")
            itmp.replace(ICLOUD_OUT)
            print(f"Wrote {ICLOUD_OUT}")
        except OSError as e:
            print(f"(iCloud copy skipped: {e})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
