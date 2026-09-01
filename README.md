# Unstuck Coach — ResonantOS add-on

Routes a messy stuck point to one humane next move, packaged as a ResonantOS
2.0.0-alpha add-on. The method is [Unstuck Coach](https://github.com/simongonzalezdc/unstuck-coach)
(MIT), a folder-based whole-person executive-function accessibility coach:
signal recognition, stance routing, safety boundaries, and protocol scripts
for starting, switching, capturing, recovering, and closing loops without
shame.

The method files are vendored byte-identical under `vendor/`, hash-pinned in
`method-pins.json`, and wrapped by a deterministic local service. The wrapper
adds no dependencies: Python 3.10+ standard library only.

## What it is (and is not)

`unstuckcoach.coach` is the method's DETERMINISTIC routing and script layer —
not a language model. Every reply is assembled from the method's own scripts
and response shapes: crisis, medication, therapy, and official-deadline
inputs route to the method's boundary scripts verbatim (never to task
coaching); brain dumps get sorted outside your head into one next move plus
held context; everything else gets one reflection, one next move, and one
tiny check.

- `unstuckcoach.status` — report service version, the pinned canonical method
  commit, and turn counters.
- `unstuckcoach.coach` — coach one stuck point (messy, multi-line brain
  dumps are canonical input); returns stance, protocols, signals, life
  surface, reply, held pile, check, and method refs.

Turns are persisted under `var/<session_id>/` with any home paths redacted
to `~` — on disk and in the response.

## Running it

    python3 server.py          # listens on http://127.0.0.1:4893 (the manifest entrypoint)

    curl -s http://127.0.0.1:4893/health
    curl -s -X POST http://127.0.0.1:4893/ -H 'Content-Type: application/json' \
      -d '{"method":"unstuckcoach.coach","params":{"stuck_point":"brain dump: dentist at 3, bill overdue, no food, buy soap"}}'

Environment: `UNSTUCK_PORT` (dev only — the manifest declares 4893). The
service spawns no processes, makes no network calls, keeps no telemetry, and
refuses any request shape the manifest does not declare.

## Tests

    python3 -m unittest discover -s tests   # 47 tests: pins, router, service, adversarial matrix, privacy

    sh run-validator-check.sh <path-to-2.0.0-alpha-clone>  # manifest vs the real validator

`vendor/` is hash-pinned to the canonical upstream commit (recorded in
`method-pins.json`); a wrapper test fails loudly if the vendored files drift
on either side, forcing a conscious re-vendor.

## License

MIT — see LICENSE. The vendored Unstuck Coach method is MIT,
[simongonzalezdc/unstuck-coach](https://github.com/simongonzalezdc/unstuck-coach).
