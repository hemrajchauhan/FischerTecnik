# Fischertechnik Factory Dashboard

Read-only monitoring and virtualisation dashboard for the Fischertechnik learning factory.

## Modes

- **Auto**: prefers live OPC UA. If live is unavailable, it uses a recorded CSV when present; otherwise it falls back to the deterministic simulation.
- **Live OPC UA**: reads the PLC/server only. The dashboard never writes to OPC UA.
- **Simulation**: runs locally without Ethernet and follows the supplied PLC process structure.
- **Replay CSV**: replays a recorded session row by row and loops at the end.

## PLC/TwinCAT basis

The registry contains the 80 variables declared in the supplied `gvl_MS`, `gvl_HBW`, `gvl_C`, `gvl_PM`, and `gvl_SL` GVLs. The screenshots/server configuration use namespace 4 and string NodeIds such as `ns=4;s=gvl_MS.bLamp_MS`.

The current live server capture confirmed only three `LocalVariables` nodes as usable dashboard telemetry:

- `LocalVariables.iC_CoordH`
- `LocalVariables.iC_CoordV`
- `LocalVariables.iC_CoordR`

The previous implementation registered 17 LocalVariables. Fourteen of those returned `BadNodeIdUnknown`, so they have been removed from the live polling registry. This prevents the dashboard from generating false telemetry failures. Emergency state and `iMS_Step` are therefore not assumed to be available over OPC UA unless they are explicitly verified on the connected server.

The color classification is copied from `p_ColorSorting.TcPOU`:

- reflection `> 220` = White
- reflection `> 100` and `<= 220` = Red
- reflection `<= 100` = Blue

## Cycle and phase timing

**Live/replay mode does not use a hard-coded cycle clock.** The production cycle starts when the vacuum gripper picks a workpiece from the HBW hand-off position (`c.valve.vacuum` rising while `hbw.sensor.outside` reports a workpiece) and ends when that workpiece reaches the sorting-line entry (`sl.sensor.before_color` active, using its active-low semantics). Because the factory is pipelined, multiple material cycles may be open at once; starts and ends are paired FIFO.

Within each cycle, phase boundaries are detected from actual PLC signal transitions:

1. Burning: `ms.process.burn` rising edge
2. Oven release: burn falling edge
3. Transfer from oven: `ms.motor.transfer_oven` rising edge
4. Transfer to turntable: `ms.motor.transfer_turntable` rising edge
5. Sawing: turntable clockwise or saw rising edge
6. Move to sorting: MS conveyor rising edge
7. Sorting: MS conveyor falling edge
8. Next material preparation: oven door / oven slider preparation after sorting

Every completed material-flow cycle is written to `process_cycles.csv` with its measured pickup-to-sorting duration. MS phase timing is kept separately in `stage_operations.csv` because the factory is pipelined and those phases cannot be safely assigned to one material cycle.

This is important for the real factory because the PLC process contains timer-based steps and the physical execution can be delayed by the current plant state. For example, a long transfer is recorded as a long transfer instead of being forced into a fixed reference duration.

The supplied live recording shows overlapping HBW pickups and downstream sorting-line arrivals. The dashboard therefore reports material-flow cycle duration separately from pickup cadence and from MS burning/stage durations.

## Pipelined process model

The factory is treated as a pipelined process. HBW and the vacuum-gripper crane can work concurrently with the multi-processing station. The dashboard therefore does not use `LocalVariables.iMS_Step` as a global activity gate.

Region highlighting is based on physical process actuators rather than idle-state sensors. Motors and relevant pneumatic valves can make a station active; compressors alone do not. Sensors, reference switches and encoder impulses are shown as diagnostics because several of them are TRUE at rest.

## Incident and unexpected-event handling

The dashboard checks the live process against the measured process model. It can detect:

- contradictory actuator commands
- an actuator active outside its allowed process phase
- unexpected PM sensor transitions
- invalid PLC process-step values when `iMS_Step` is actually exposed
- process-phase timeouts
- OPC UA disconnects, stale telemetry and timestamp spread warnings

Events are edge-triggered so a persistent problem does not create a new alarm every refresh. The live recorder stores the event and creates an incident recording with pre-event and post-event telemetry.

## Live connection behavior

The OPC UA reader runs in a background thread and keeps the last good telemetry snapshot. If the connection drops, the dashboard reports **STALE / LAST KNOWN** data instead of pretending that the values are live. If no good snapshot exists, it reports **OFFLINE**.

The diagnostics panel shows endpoint, namespace, received values, good/bad counts, source timestamps, server timestamps and timestamp spread.

## Emergency behavior

The dashboard is read-only. It does not reset, start, stop, or command the factory.

The physical emergency stop and PLC safety logic remain authoritative. The dashboard should only display an explicit emergency signal when that signal is actually exposed and verified by the connected OPC UA server. It must not infer an emergency merely because all outputs become false, because an idle plant has the same output pattern.

## Virtual factory

The supplied factory photograph is the background. Transparent polygons highlight HBW, crane, machining station, punching machine, and sorting line. Region state is derived from physical process outputs. Polygon coordinates are centralized in `visualization.py`.

## Run with uv

```bash
uv sync
cp .env.example .env
uv run streamlit run src/factory_dashboard/app.py
```

For local development without the factory Ethernet, use **Simulation** or **Auto**. For the real plant, connect the PC to the factory Ethernet and select **Live OPC UA** or **Auto**.

## Tests

The project includes tests for:

- exact PLC color thresholds
- emergency visualization behavior when an explicit emergency state is supplied
- stale-data behavior
- actual burn-edge cycle timing
- actual signal-based phase-duration measurement
- unexpected sensor-transition detection without false alarms from idle TRUE sensors
- simulation cycle/phase/color behavior
- replay row progression and looping
- verified OPC UA node registry
- region activity and MS process phase interpretation

## Cake-factory dashboard views

The presentation layer has four focused views:

- **Line Operator — Oven & line cockpit**: live status, emergency/critical/warning attention, cake cycle KPIs, cycle-cadence plot, simulated oven-temperature plot, and current process stage. This is the only operational view with prominent warning/critical handling and the virtual factory.
- **QA Manager — Quality control room**: Vanilla/Strawberry/Blueberry production counts, NOK cakes, September complaints, latest cake table, and burn-time quality plot.
- **Department Manager — Cake production overview**: OEE, availability, quality, complaints trend, and OEE component plot. No plant diagnostics or alarm detail is exposed here.
- **Factory Diagnostics — Virtualisation & signal diagnostics**: full virtual factory, station states, observed stage-duration distributions, station activity, event timeline, and current sensor/actuator values. This is the technical inspection view rather than a management view.

The presentation is intentionally framed as a **Smart Cake Factory**: HBW is ingredient/tray storage, the crane is cake handling, the MS station is the baking oven and processing area, PM is decoration/finishing, and SL is quality inspection and dispatch. The physical Fischertechnik image remains the visual plant model.

All plots are Plotly 2-D visuals using the TUM-style palette from `DASHBOARD_SPEC_1.md`: TUM blue for primary process data, light blue for supporting data, orange with hatching for anomalies, green only for the case-study complaint reduction, and no red/green combination.

## Timing model

Live/replay timing is measured from telemetry transitions rather than a fixed reference duration. In particular, **Burning is the exact interval while `ms.process.burn` / `gvl_MS.bLamp_MS` is TRUE**. Other operations use their real actuator ON/OFF timestamps. For process windows where waiting can occur, the analytics keep both:

- `elapsed_s`: total observed stage window
- `actuator_on_s`: time the responsible actuator(s) were actually ON

This prevents a long wait from being hidden inside a nominal stage duration.

### Plot-ready recording outputs

Every live session keeps the raw files and additionally generates reproducible analytical files:

- `telemetry.csv` — raw recorded variables
- `telemetry_clean.csv` — cleaned, timestamp-sorted, de-duplicated telemetry with sample intervals and station activity flags
- `process_cycles.csv` — process-monitor cycle records
- `cycle_metrics.csv` — burn-edge-to-burn-edge cycle measurements
- `stage_operations.csv` — actual stage/actuator duration observations
- `events.jsonl` — process, safety and telemetry events
- `kpi_summary.json` — summary KPIs for management views

The raw telemetry remains the source of truth. The derived files can always be regenerated from it.

## Telemetry semantics and timestamp handling

The dashboard keeps raw PLC values unchanged and interprets sensor polarity separately.
The MS oven light barrier is configured as active-low based on the observed idle/run recording:
`ms.sensor.oven=True` means the beam is clear and `False` means a workpiece is detected.
Unexpected sensor events therefore use physical activation edges rather than assuming every
sensor is active on a raw FALSE-to-TRUE transition.

For live OPC UA data, every node SourceTimestamp and ServerTimestamp is recorded in
`telemetry_timestamps.csv`. Process cycle and stage boundaries use the SourceTimestamp of
the signal that caused the transition when available. `source_timestamp_reference` and
`sync_spread_ms` are also persisted for each snapshot. This keeps raw capture auditable while
making duration and cycle analytics independent of dashboard polling/read-order latency.

The main station chart is labelled **Observed station activity** rather than utilization:
activity is based on configured physical process outputs and is not a formal machine
availability/OEE utilization metric.

## Replay navigator

Replay mode now supports:
- selecting a specific recorded session
- seeing the exact recording start/end time in UTC
- selecting a recorded incident from that session
- jumping directly to an incident timestamp
- jumping to an arbitrary date/time in the recording
- play/pause and replay speed controls (1x, 2x, 5x, 10x, 25x)
- start/restart controls

Replay always uses the selected session's `telemetry.csv` plus its
`telemetry_timestamps.csv` sidecar, so source timestamps are preserved.
Incident clips are stored under the corresponding session's `incidents/`
directory. Older recordings whose incident metadata is still under the
legacy `data/incidents/` location remain discoverable when the metadata points
to the selected session.
