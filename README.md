# polymer_indent

Main controller + station workers for the PEGDA UV-cure / indentation workcell.
Replaces `denos`, built on the cleaned-up CubOS YAML interfaces.

```
                bear-den-keeper  (10.210.29.11, win10)
        ┌──────────────────────────────────────────────┐
        │  polymer_indent  (main.py / `polymer-indent`) │
        │   experiment loop · arm transfers · Opentrons │
        │   (placeholder) · SQLite bookkeeping          │
        └───┬──────────────┬───────────────┬────────────┘
       HTTP │  (placeholder)│         HTTP  │  HTTP
            │     ┌─────────▼──┐    ┌───────▼─────────┐   ┌───────────────┐
            │     │ Opentrons  │    │ bear-den-scale  │   │ bear-den-asmi │
            │     │  Flex      │    │ station_worker  │   │ station_worker│
            │     └────────────┘    │  + cubos@stg    │   │  + cubos@stg  │
            │                       │  uv_curing      │   │  asmi         │
       ┌────▼─────┐                 │ user: sartorius-│   │ user: asmi    │
       │ xArm +   │                 │       scale     │   │               │
       │ Vention  │                 └─────────────────┘   └───────────────┘
       │ rail     │  10.210.29.16:5004  10.210.29.12:8000   10.210.29.17:8000
       └──────────┘
```

| Role | Device | IP | OS | Login user | What runs there |
|------|--------|----|----|------------|-----------------|
| Controller | `bear-den-keeper` | 10.210.29.11 | win10 | Kab Lab | `polymer_indent` (`main.py` / `polymer-indent`) |
| UV-curing station ("sharc") | `bear-den-scale` | 10.210.29.12 | debian | `sartorius-scale` | `station_worker --config configs/stations/sharc.yaml` + cubos@staging |
| ASMI station | `bear-den-asmi` | 10.210.29.17 | debian | `asmi` | `station_worker --config configs/stations/asmi.yaml` + cubos@staging |
| Arm + rail | `bear-den-arm1` (xArm) 10.210.29.16 / `bear-den-vention` 10.210.29.15 | arm worker `:5004` | — | — | existing denos `arm_worker.py` (out of scope here; in denos it runs on keeper) |
| Opentrons | Flex 10.210.29.218 | shim `:5003` | — | — | placeholder client only |

## The clean split

- **Main controller** (`polymer_indent/`, this machine): runs the per-well
  experiment loop, calls Opentrons (placeholder), drives the arm, and does
  result bookkeeping. **No cubos dependency** — it just reads frozen YAMLs,
  swaps a well id into a base protocol, and POSTs `{gantry, deck, protocol}` to
  the station Pi.
- **SHARC Pi** (`station_worker/` + `cubos@staging`): fixed CubOS gantry/deck
  for the UV station; receives one protocol YAML per well, runs it, returns
  results.
- **ASMI Pi** (`station_worker/` + `cubos@staging`): fixed CubOS gantry/deck for
  the ASMI station; same.
- **Protocol YAMLs** are the frozen cubos base protocols with the well id
  rewritten in memory by the main loop (a one-line text edit — see
  `polymer_indent/protocol_render.py`). The gantry and deck YAMLs are sent
  byte-for-byte every iteration.

## The loop (per well)

```
opentrons.run_fill(well, volume_ul, formulation)            # PLACEHOLDER
arm.transfer(opentrons -> uv_station)
sharc.run_protocol(render_protocol(sharc_base, well))       # cubos on the Pi
arm.transfer(uv_station -> asmi)
asmi.run_protocol(render_protocol(asmi_base, well))         # cubos on the Pi
results.store(experiment_id, well, sharc, asmi, <both protocol YAMLs>)
arm.transfer(asmi -> storage_end if last well else opentrons)
```

## Layout

```
configs/
  controller.yaml                  device URLs, per-station file bundles, db path  (TODO: confirm IPs)
  gantry/sharc_gantry.yaml         verbatim copy of cubos@staging configs/gantry/cub_sharc.yaml
  gantry/asmi_gantry.yaml          verbatim copy of ASMI_new   configs/gantry/asmi_gantry.yaml
  deck/sharc_deck.yaml             verbatim copy of cubos@staging configs/deck/sharc_uv_deck.yaml
  deck/asmi_deck.yaml              verbatim copy of ASMI_new   configs/deck/asmi_deck.yaml
  protocol/sharc_uv_one_well.yaml  one-well UV `measure` (cubos format; cubos ships only a 96-well scan)
  protocol/asmi_indentation_test.yaml  verbatim copy of ASMI_new (one-well `measure`)
  protocol/asmi_indentation.yaml   verbatim copy of ASMI_new (full-plate scan; reference only)
  protocol/sharc_uv_curing_scan.yaml   verbatim copy of cubos@staging (full-plate scan; reference only)
  stations/{sharc,asmi}.yaml       station-worker server config (port, run dir, allow-list)
polymer_indent/                    the controller package (no cubos dep)
  cli.py  experiment.py  protocol_render.py  results.py  loop.py  config.py
  clients/{cubos_station,arm_rail,opentrons}.py
station_worker/                    the Flask worker run on each station Pi (imports cubos)
  app.py  worker.py  config.py  runs.py  allow.py  jsonify.py  __main__.py
arm_worker/                        xArm + Vention-rail transfer worker (runs on the controller box)
  app.py  positions.py  __main__.py            (extracted from denos; --mock = no hardware)
deploy/                            systemd units + install_station.sh
examples/pegda_screen.yaml         sample experiment
scripts/test_asmi.py  test_uv.py  test_arm.py   per-device test runners
main.py                            controller entrypoint
tests/                             pytest suite
```

## Test one device at a time

Standalone runners you launch **from the controller box** — they drive the
machine over HTTP (the cpu never runs cubos / the arm SDK; the protocol runs on
the Pi / the arm worker). Use `--mock` first to confirm the YAMLs + the Flask API
without moving anything:

```bash
# ASMI station — run one full indentation protocol on one well
python scripts/test_asmi.py --well E5 --mock              # Pi loads gantry+deck+protocol, runs cubos in mock (no hardware)
python scripts/test_asmi.py --well E5                     # real run on the ASMI Pi (prompts first)
python scripts/test_asmi.py --well B3 --force-limit 5 --indentation-limit-height -3
python scripts/test_asmi.py --validate-only --well E5     # just the Pi's offline cubos setup_protocol

# SHARC / UV-curing station
python scripts/test_uv.py --well A1 --mock
python scripts/test_uv.py --well A1                        # real (prompts first)
python scripts/test_uv.py --well C7 --intensity 20 --exposure-time 300

# Arm + Vention rail — run one plate transfer
python scripts/test_arm.py --from opentrons --to uv_station --mock   # arm worker uses logging-only stand-ins
python scripts/test_arm.py --from opentrons --to uv_station          # real (prompts first)
python scripts/test_arm.py --from asmi --to storage_end
python scripts/test_arm.py --health
```

Each reads the device's `base_url` (and, for stations, the gantry/deck/base-protocol
paths) from `configs/controller.yaml`; override with `--url` (and `--gantry`,
`--deck`, `--protocol` for stations). `--help` on any of them lists every knob.
(Pointing a station's `--protocol` at a scan file runs the whole plate, but then
the station's `allow`-list must include the `scan` command.)

## Install

Controller (this machine):

```bash
pip install -e .          # pyyaml + requests
```

Each station Pi (`pip` already has cubos via the repo checkouts; the extra makes
a clean-machine install work too):

```bash
git clone <this repo> ~/polymer_indent && cd ~/polymer_indent
./deploy/install_station.sh sharc      # or:  asmi
# then follow the printed systemd steps
```

Arm worker (runs on the controller box, `bear-den-keeper`). `--mock` needs only
flask; real mode needs Python 3.10 (for `machine-logic-sdk`), the `arm` extra,
and the `keeper_pc` repo's `device_drivers/` on `PYTHONPATH` (that's where
`VentionRailway` lives):

```bash
pip install -e ".[arm]"                # flask + xarm-python-sdk + machine-logic-sdk
# real mode also needs, e.g.:  export PYTHONPATH=/path/to/keeper_pc:$PYTHONPATH
```

## Run

Start each station worker on its Pi, and the arm worker on the controller box:

```bash
python -m station_worker --config configs/stations/sharc.yaml      # on the SHARC Pi
python -m station_worker --config configs/stations/asmi.yaml       # on the ASMI Pi
python -m arm_worker                                               # on keeper (port 5004); add --mock for no hardware
```

Then from the controller:

```bash
# offline pre-flight: validate every well's protocols on the Pis (no hardware)
polymer-indent validate examples/pegda_screen.yaml

# dry run end to end (stations skip all hardware)
polymer-indent run examples/pegda_screen.yaml --mock

# real run
polymer-indent run examples/pegda_screen.yaml
polymer-indent run examples/pegda_screen.yaml --resume          # skip wells already done
polymer-indent run examples/pegda_screen.yaml --only-well A1,B2
polymer-indent run examples/pegda_screen.yaml --continue-on-error

# ping everything
polymer-indent health
```

(`python main.py …` is equivalent to the `polymer-indent` console script.)

## Station HTTP API (`station_worker`)

| Method & path           | Body                                                              | Returns |
|-------------------------|-------------------------------------------------------------------|---------|
| `GET /health`           | —                                                                 | `{status, station_id, cubos_version, busy, current_run_id, allow}` |
| `POST /validate-protocol` | `{gantry_config, deck_config, protocol_yaml}`                   | `{valid: bool, steps?, error?}` (offline `setup_protocol`, no hardware) |
| `POST /run-protocol`    | `{run_id, gantry_config, deck_config, protocol_yaml, mock_mode?, metadata?}` | `{success, run_id, station_id, results, cubos_version, protocol_sha256, artifacts}` — or `{success:false, error, traceback}` (500); `409` if a run is in progress |
| `POST /stop`            | —                                                                 | best-effort only — cubos has no mid-`protocol.run()` abort; use a hardware kill switch |
| `GET /runs/<run_id>`    | —                                                                 | `{run_id, run_dir, protocol_yaml, result, error?}` (404 if unknown) |

On `/run-protocol` the worker: takes the process-wide station lock (one CubOS
protocol at a time per Pi), checks the protocol against the station `allow`-list
(instrument & command names), writes `gantry.yaml` / `deck.yaml` / `protocol.yaml`
+ `meta.json` into `run_dir/<sanitized run_id>/`, then — for a **real run** —
mirrors cubos' `setup/run_protocol.py`: `Gantry(config=…)` → `setup_protocol(…, gantry=gantry)`
→ `gantry.connect()` → `gantry.prepare_for_protocol_run()` → `board.connect_instruments()`
→ health check → `protocol.run(context)` → `finally` disconnect instruments + gantry.
A **mock run** (`mock_mode=true`) uses `setup_protocol(gantry=None, mock_mode=True)`
and touches no hardware. The result JSON is written next to the inputs.

## Arm-transfer HTTP API (`arm_worker`)

| Method & path | Body | Returns |
|---|---|---|
| `GET /health` | — | `{status, device:"xarm", mock_mode_default, busy, current, routes}` |
| `POST /run` | `{"from": <loc>, "to": <loc>, "run_id"?, "mock_mode"?}` | `{success, from, to, run_id, mock}` — or `{success:false, error}` (400 bad/unknown route, 409 busy, 500 transfer error) |
| `POST /stop` | — | best-effort: sets a stop flag and (real mode) `set_state(4)` / stops the gripper & rail |

Locations: `opentrons`, `uv_station`, `asmi`, `storage_end`. Routes: `opentrons→uv_station`,
`uv_station→asmi`, `asmi→uv_station`, `asmi→opentrons`, `asmi→storage_end`, `opentrons→storage_end`.
One transfer at a time (process lock). The pick/place sequences + named poses are
in `arm_worker/positions.py` (lifted verbatim from denos). `mock_mode` runs the
same sequence against logging-only stand-ins — no xArm / rail / SDK imports.

## Hardware safety

`station_worker` drives real GRBL gantries and instruments via cubos. Before any
non-mock run on a Pi:

1. Make sure the Pi's cubos is `@staging` and the deck calibration anchors are
   correct (the copied `configs/deck/asmi_deck.yaml` still carries upstream's
   "TODO re-measure" markers; `cub_sharc.yaml` ships `uv_curing` with
   `offline: true` — clear that flag in the gantry YAML when you actually want
   the lamp to fire).
2. `python cubos/setup/validate_setup.py <gantry> <deck> <a-generated-protocol>`.
3. `python cubos/setup/hello_world.py --gantry <gantry>` jog test.
4. `polymer-indent validate <experiment.yaml>` (offline `setup_protocol` on each Pi).
5. `polymer-indent run <experiment.yaml> --mock` end-to-end dry run.

## Bookkeeping

SQLite at `results/polymer_indent.db` (`results.db_path` in `controller.yaml`):
`experiments`, `wells`, `runs` (raw protocol YAML + result JSON kept as TEXT for
replay/audit). Each Pi also keeps its own per-run directories under `run_dir`.

## Status / TODO

- **Opentrons is a placeholder** (`polymer_indent/clients/opentrons.py`): logs
  the requested fill and returns success; the real Flex REST flow is a commented
  stub.
- Per-well *protocol* overrides aren't wired yet — only the well id is swapped
  into the base protocol. Per-well params (intensity, exposure, force limit, …)
  are recorded with the results; extend `render_protocol` / the base files to
  vary them per well.
- The arm transfer worker (`arm_worker/`, extracted from denos) runs on the
  controller box; `configs/controller.yaml` has `arm.base_url` at `10.210.29.16:5004`
  (the arm's own IP, per the design-doc loop) — in denos this worker actually ran
  on keeper, so switch it to `http://localhost:5004` if you run it there. Its
  poses (`arm_worker/positions.py`) and the Opentrons-D1 plate variant should be
  re-checked against the current rig before a real transfer.
- IPs are set: `bear-den-scale` 10.210.29.12 ("sharc"/UV-curing), `bear-den-asmi`
  10.210.29.17, xArm `bear-den-arm1` 10.210.29.16 (+ Vention rail 10.210.29.15),
  Opentrons Flex 10.210.29.218. Confirm the ASMI park position in
  `configs/protocol/asmi_indentation_test.yaml`.
- `/stop` is best-effort only; a real emergency stop must be hardware.

## Tests

```bash
pip install -e ".[dev]"      # adds pytest; install flask too for the worker tests
pytest -q
```

Tests that need cubos installed (`protocol_engine`) skip cleanly if it's absent;
the SHARC mock-run test also skips on a cubos build that lacks the SHARC holder
labware definition.
