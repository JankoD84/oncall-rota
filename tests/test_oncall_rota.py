import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oncall_rota as oc  # noqa: E402

CFG = {"start": "2026-11-30", "shift_days": 7, "min_rest_shifts": 1, "handover": "10:00",
       "holidays": ["2026-12-24", "2026-12-25", "2026-12-26", "2027-01-01"],
       "people": [{"name": "Alice", "handle": "@alice", "pto": ["2026-12-21..2027-01-03"]}, {"name": "Bob"}, {"name": "Cara"}, {"name": "Dan"}]}


def test_pto_respected_and_fair():
    shifts, people, warnings = oc.generate(CFG, 16)
    assert len(shifts) == 16 and not warnings
    for s in shifts:
        if s.person == "Alice":
            assert not (s.start <= dt.date(2027, 1, 3) and dt.date(2026, 12, 21) <= s.end)
    assert {p.shifts for p in people} == {4}
    assert oc.fairness(people) < 8  # less than one normal shift, despite holiday weeks Alice cannot take
    for a, b in zip(shifts, shifts[1:]):
        assert a.person != b.person          # rest rule


def test_holiday_weeks_spread():
    shifts, people, _ = oc.generate(CFG, 52)
    hol = {p.name: p.holiday_days for p in people}
    assert max(hol.values()) - min(hol.values()) <= 3


def test_overrides_and_now():
    shifts, _, _ = oc.generate(CFG, 4, {"2026-12-07": "Dan"})
    assert shifts[1].person == "Dan" and shifts[1].override
    cur, nxt = oc.who_now(shifts, dt.date(2026, 12, 9))
    assert cur.person == "Dan" and nxt.start == dt.date(2026, 12, 14)


def test_gap_warning_when_everyone_away():
    cfg = dict(CFG, people=[{"name": "A", "pto": ["2026-11-30..2026-12-06"]}, {"name": "B", "pto": ["2026-11-30..2026-12-06"]}])
    _, _, warnings = oc.generate(cfg, 1)
    assert any("EVERYONE" in w for w in warnings)


def test_errors():
    import pytest
    with pytest.raises(ValueError):
        oc.generate(dict(CFG, people=[{"name": "Solo"}]), 4)
    with pytest.raises(ValueError):
        oc.generate(CFG, 4, {"2026-11-30": "Nobody"})


def test_ics():
    shifts, people, _ = oc.generate(CFG, 2)
    ics = oc.to_ics(shifts, people, "10:00", "Team")
    assert ics.count("BEGIN:VEVENT") == 2 and "DTSTART:20261130T100000" in ics and "\r\n" in ics


def test_cli_swap_now_ics(tmp_path, capsys):
    cfgp = tmp_path / "rota.json"
    cfgp.write_text(json.dumps(CFG))
    assert oc.main(["generate", str(cfgp), "--weeks", "4"]) == 0
    assert "| Week of |" in capsys.readouterr().out
    assert oc.main(["swap", str(cfgp), "--date", "2026-12-09", "--to", "Cara"]) == 0
    assert json.loads((tmp_path / "rota.overrides.json").read_text()) == {"2026-12-07": "Cara"}
    capsys.readouterr()
    assert oc.main(["now", str(cfgp), "--date", "2026-12-09", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["on_call"] == "Cara"
    assert oc.main(["ics", str(cfgp), "--out", str(tmp_path / "r.ics")]) == 0
    assert (tmp_path / "r.ics").read_text().startswith("BEGIN:VCALENDAR")
    assert oc.main(["swap", str(cfgp), "--date", "2026-12-09", "--to", "Zed"]) == 2
