#!/usr/bin/env python3
"""oncall-rota: a fair on-call rotation that respects PTO and public holidays — from one YAML file.

  generate   build the rota for N weeks; balances weekend days, holiday days and total load,
             keeps a rest period between shifts, never schedules someone on PTO
  now        who is on call right now and who is next (for a bot or a Slack channel topic)
  swap       record an override ("Bob takes Alice's week of 14 Dec") that survives regeneration
  ics        export an iCal feed people can subscribe to

Config (rota.yml):
  start: 2026-11-30          # first shift start (date)
  shift_days: 7
  handover: "10:00"
  timezone_note: "Europe/Prague"
  min_rest_shifts: 1          # shifts off after a shift
  holidays: [2026-12-24, 2026-12-25]   # or fetch with --holidays-country CZ
  weights: {weekday: 1, weekend: 1.5, holiday: 3}
  people:
    - {name: Alice, handle: "@alice", pto: ["2026-12-21..2027-01-03"]}
    - {name: Bob, handle: "@bob"}
  history: {Alice: 4.5, Bob: 2}   # optional load carried over from earlier rotas
Standard library only (PyYAML for YAML configs; JSON works without it).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

__version__ = "1.0.0"


@dataclass
class Shift:
    start: dt.date
    end: dt.date                       # inclusive
    person: str
    load: float
    weekend_days: int
    holiday_days: int
    override: bool = False


@dataclass
class Person:
    name: str
    handle: str = ""
    pto: list[tuple[dt.date, dt.date]] = field(default_factory=list)
    load: float = 0.0
    weekend_days: int = 0
    holiday_days: int = 0
    shifts: int = 0
    last_index: int = -999


def parse_range(s: str) -> tuple[dt.date, dt.date]:
    a, _, b = str(s).partition("..")
    start = dt.date.fromisoformat(a.strip())
    return start, dt.date.fromisoformat(b.strip()) if b else start


def load_config(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yml", ".yaml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            sys.exit("oncall-rota: PyYAML is needed for YAML configs (pip install pyyaml) or use JSON")
        return yaml.safe_load(text) or {}
    return json.loads(text)


def fetch_holidays(country: str, years: set[int]) -> list[str]:
    out = []
    for y in sorted(years):
        req = urllib.request.Request(f"https://date.nager.at/api/v3/PublicHolidays/{y}/{country}", headers={"User-Agent": "oncall-rota"})
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - fixed public holiday API
            out += [h["date"] for h in json.loads(r.read()) if h.get("global", True)]
    return out


def shift_cost(start: dt.date, days: int, holidays: set[dt.date], w: dict) -> tuple[float, int, int]:
    load, we, ho = 0.0, 0, 0
    for i in range(days):
        d = start + dt.timedelta(days=i)
        if d in holidays:
            load += w.get("holiday", 3)
            ho += 1
        elif d.weekday() >= 5:
            load += w.get("weekend", 1.5)
            we += 1
        else:
            load += w.get("weekday", 1)
    return load, we, ho


def available(p: Person, start: dt.date, end: dt.date) -> bool:
    return not any(a <= end and start <= b for a, b in p.pto)


def generate(cfg: dict, weeks: int, overrides: dict | None = None, holidays_extra: list[str] | None = None) -> tuple[list[Shift], list[Person], list[str]]:
    days = int(cfg.get("shift_days", 7))
    rest = int(cfg.get("min_rest_shifts", 1))
    w = cfg.get("weights", {})
    holidays = {dt.date.fromisoformat(str(h)) for h in (cfg.get("holidays") or []) + (holidays_extra or [])}
    hist = cfg.get("history") or {}
    people = [Person(p["name"], p.get("handle", ""), [parse_range(r) for r in p.get("pto", [])], float(hist.get(p["name"], 0)))
              for p in cfg.get("people", [])]
    if len(people) < 2:
        raise ValueError("need at least 2 people for a rotation")
    by_name = {p.name: p for p in people}
    overrides = overrides or {}
    start = dt.date.fromisoformat(str(cfg["start"]))
    n_shifts = max(1, (weeks * 7) // days)
    shifts, warnings = [], []
    for i in range(n_shifts):
        s = start + dt.timedelta(days=i * days)
        e = s + dt.timedelta(days=days - 1)
        load, we, ho = shift_cost(s, days, holidays, w)
        forced = overrides.get(s.isoformat())
        if forced:
            if forced not in by_name:
                raise ValueError(f"override for {s} names unknown person '{forced}'")
            chosen = by_name[forced]
            if not available(chosen, s, e):
                warnings.append(f"{s}: override puts {forced} on call during their PTO")
        else:
            cands = [p for p in people if available(p, s, e) and i - p.last_index > rest]
            if not cands:
                cands = [p for p in people if available(p, s, e)]
                if cands:
                    warnings.append(f"{s}: rest rule broken, not enough people available")
            if not cands:
                cands = people
                warnings.append(f"{s}: EVERYONE is on PTO — coverage gap, assigned by load anyway")
            chosen = min(cands, key=lambda p: (p.load, p.holiday_days if ho else 0, p.weekend_days if we else 0, p.last_index, p.name))
        chosen.load += load
        chosen.weekend_days += we
        chosen.holiday_days += ho
        chosen.shifts += 1
        chosen.last_index = i
        shifts.append(Shift(s, e, chosen.name, load, we, ho, bool(forced)))
    return shifts, people, warnings


def fairness(people: list[Person]) -> float:
    loads = [p.load for p in people]
    return round(max(loads) - min(loads), 2)


def who_now(shifts: list[Shift], when: dt.date) -> tuple[Shift | None, Shift | None]:
    cur = next((s for s in shifts if s.start <= when <= s.end), None)
    nxt = next((s for s in shifts if s.start > when), None)
    return cur, nxt


def to_ics(shifts: list[Shift], people: list[Person], handover: str, calname: str) -> str:
    hh, mm = (handover or "10:00").split(":")
    handles = {p.name: p.handle for p in people}
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Dulvarn//oncall-rota//EN", f"X-WR-CALNAME:{calname}", "CALSCALE:GREGORIAN"]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for s in shifts:
        end = s.end + dt.timedelta(days=1)
        uid = uuid.uuid5(uuid.NAMESPACE_URL, f"oncall-rota/{calname}/{s.start.isoformat()}")
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
                  f"DTSTART:{s.start.strftime('%Y%m%d')}T{hh}{mm}00", f"DTEND:{end.strftime('%Y%m%d')}T{hh}{mm}00",
                  f"SUMMARY:On call: {s.person}", f"DESCRIPTION:{handles.get(s.person, '')} · load {s.load} · weekend days {s.weekend_days} · holidays {s.holiday_days}",
                  "TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def render(shifts, people, warnings, fmt):
    if fmt == "json":
        return json.dumps({"shifts": [dict(s.__dict__, start=s.start.isoformat(), end=s.end.isoformat()) for s in shifts],
                           "people": [{"name": p.name, "load": p.load, "shifts": p.shifts, "weekend_days": p.weekend_days,
                                       "holiday_days": p.holiday_days} for p in people],
                           "spread": fairness(people), "warnings": warnings}, indent=2)
    out = ["| Week of | Until | On call | Weekend days | Holidays |", "|---|---|---|---|---|"]
    out += [f"| {s.start} | {s.end} | {s.person}{' (swap)' if s.override else ''} | {s.weekend_days} | {s.holiday_days} |" for s in shifts]
    out += ["", "| Person | Shifts | Load | Weekend days | Holiday days |", "|---|---|---|---|---|"]
    out += [f"| {p.name} | {p.shifts} | {p.load} | {p.weekend_days} | {p.holiday_days} |" for p in sorted(people, key=lambda p: -p.load)]
    out += ["", f"Load spread (max − min): {fairness(people)}"]
    out += [f"⚠️ {w}" for w in warnings]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="oncall-rota", description="Fair on-call rotation with PTO and holidays")
    ap.add_argument("command", choices=["generate", "now", "swap", "ics"])
    ap.add_argument("config", type=Path)
    ap.add_argument("--weeks", type=int, default=12)
    ap.add_argument("--overrides", type=Path, help="JSON file of swaps (default: <config>.overrides.json)")
    ap.add_argument("--holidays-country", help="fetch public holidays from Nager.Date (e.g. CZ, SK, DE, US)")
    ap.add_argument("--date", help="for now/swap: YYYY-MM-DD (swap: any day inside the shift)")
    ap.add_argument("--to", help="for swap: who takes the shift")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--out", type=Path, help="write output to a file (ics)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = ap.parse_args(argv)
    if not a.config.is_file():
        print(f"oncall-rota: {a.config} not found", file=sys.stderr)
        return 2
    cfg = load_config(a.config)
    ov_path = a.overrides or a.config.with_suffix(".overrides.json")
    overrides = json.loads(ov_path.read_text()) if ov_path.is_file() else {}
    extra = []
    if a.holidays_country:
        start = dt.date.fromisoformat(str(cfg["start"]))
        extra = fetch_holidays(a.holidays_country, {start.year, (start + dt.timedelta(weeks=a.weeks)).year})
    try:
        shifts, people, warnings = generate(cfg, a.weeks, overrides, extra)
    except (ValueError, KeyError) as exc:
        print(f"oncall-rota: {exc}", file=sys.stderr)
        return 2
    if a.command == "generate":
        print(render(shifts, people, warnings, a.format))
    elif a.command == "now":
        when = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
        cur, nxt = who_now(shifts, when)
        handles = {p.name: p.handle or p.name for p in people}
        msg = {"on_call": cur.person if cur else None, "handle": handles.get(cur.person) if cur else None,
               "until": cur.end.isoformat() if cur else None, "next": nxt.person if nxt else None,
               "next_from": nxt.start.isoformat() if nxt else None}
        print(json.dumps(msg) if a.format == "json" else
              f"On call: {msg['handle'] or '—'} until {msg['until']} · next: {handles.get(msg['next'], '—')} from {msg['next_from']}")
    elif a.command == "swap":
        if not (a.date and a.to):
            print("oncall-rota: swap needs --date and --to", file=sys.stderr)
            return 2
        day = dt.date.fromisoformat(a.date)
        shift = next((s for s in shifts if s.start <= day <= s.end), None)
        if not shift:
            print("oncall-rota: no shift on that date", file=sys.stderr)
            return 2
        if a.to not in {p.name for p in people}:
            print(f"oncall-rota: unknown person {a.to}", file=sys.stderr)
            return 2
        overrides[shift.start.isoformat()] = a.to
        ov_path.write_text(json.dumps(overrides, indent=2, sort_keys=True))
        print(f"Swap saved: {a.to} takes {shift.start}–{shift.end} (was {shift.person}). Later weeks rebalance automatically.")
    elif a.command == "ics":
        ics = to_ics(shifts, people, str(cfg.get("handover", "10:00")), str(cfg.get("name", "On call")))
        if a.out:
            a.out.write_text(ics)
        else:
            sys.stdout.write(ics)
    if any("EVERYONE" in w for w in warnings):
        print("\n".join(w for w in warnings if "EVERYONE" in w), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
