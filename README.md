# oncall-rota

**A fair on-call rotation from one YAML file** — PTO respected, public holidays weighted, swaps recorded, iCal for everyone's calendar. Made for teams that run on-call from a spreadsheet and keep getting it wrong.

```bash
python3 oncall_rota.py generate rota.yml --weeks 26 --holidays-country CZ
python3 oncall_rota.py now rota.yml                     # "On call: @bob until 2026-12-13 · next: @cara"
python3 oncall_rota.py swap rota.yml --date 2026-12-09 --to Dan
python3 oncall_rota.py ics rota.yml --out oncall.ics     # subscribe in Google/Outlook/Apple Calendar
```

**Fairness, not just round-robin.** Every shift has a load: weekday 1, weekend day 1.5, public holiday 3 (configurable). The next shift goes to the available person with the lowest load so far, which spreads weekends and holidays, not only weeks. `history` carries last year's load forward, so the person who did Christmas last year is not first in line again.

**Rules it never breaks silently:** nobody is scheduled during PTO; a rest period between shifts (`min_rest_shifts`); if everyone is away the tool assigns by load *and* exits with code 1 and a coverage-gap warning.

**Swaps** are stored in `rota.overrides.json`, survive regeneration, and the following weeks rebalance automatically.

Use `now --format json` from a bot or a cron job to update a Slack channel topic. Python 3.10+, PyYAML for YAML (JSON works without it). MIT License.

## In chat

**teambot** reads this same file (`[oncall] rota = "rota.yml"`): `oncall` shows who is on call now and next, `oncall swap 2026-12-24 Bob` records a swap that later weeks rebalance around, and the Monday handoff message is posted automatically.

## About

Built by **Ján Ďuriš**, QA Automation Engineer (Playwright, Python, CI/CD). Part of a small set of open tools for release quality: [release-ready](https://github.com/JankoD84/release-ready) · [gates-doctor](https://github.com/JankoD84/gates-doctor) · [flaky-playwright-lab](https://github.com/JankoD84/flaky-playwright-lab) · [oncall-rota](https://github.com/JankoD84/oncall-rota) · [expiry-radar](https://github.com/JankoD84/expiry-radar) · [dependabot-tamer](https://github.com/JankoD84/dependabot-tamer)

Flaky tests, slow CI or releases nobody trusts? Find me on [Upwork](https://www.upwork.com/freelancers/~0198692795e824559b) or [LinkedIn](https://www.linkedin.com/in/jduris).
