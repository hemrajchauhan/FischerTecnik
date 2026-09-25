# Fischertechnik Factory Dashboard

Read-only monitoring and virtualisation dashboard for the Fischertechnik learning factory.

## Modes

- **Auto**: prefers live OPC UA. If live is unavailable, it uses a recorded CSV when present; otherwise it falls back to the deterministic simulation.
- **Live OPC UA**: reads the PLC/server only. The dashboard never writes to OPC UA.
- **Simulation**: runs locally without Ethernet and follows the supplied PLC process structure.
- **Replay CSV**: replays recorded telemetry according to its original timestamps and loops at the end.

## PLC/TwinCAT basis

The registry contains the 80 variables declared in the supplied `gvl_MS`, `gvl_HBW`, `gvl_C`, `gvl_PM`, and `gvl_SL` GVLs. The screenshots/server configuration use namespace 4 and string NodeIds such as `ns=4;s=gvl_MS.bLamp_MS`.

`LocalVariables` are treated as optional because the supplied project does not mark them with the same OPC UA data-access attribute as the GVL process variables. If the server exposes them, the dashboard uses them for emergency state, MS process step, and sorting state; otherwise it derives status from the exposed GVL signals.

The color classification is copied from `p_ColorSorting.TcPOU`:

- reflection `> 220` = White
- reflection `> 100` and `<= 220` = Red
- reflection `<= 100` = Blue

The burning process follows the supplied `p_Burning.TcPOU` sequence conceptually. Simulation timings are shortened for demonstration; they are not intended to reproduce PLC scan counts exactly.

## Recording and fallback

When `RECORD_LIVE=true`, every newly received good live OPC UA snapshot is appended to a timestamped CSV under `data/recordings/`. Duplicate timestamps are ignored. The newest recording is selected automatically for Replay and Auto fallback.

Replay uses the timestamps stored in the CSV rather than assuming one row per dashboard refresh. `REPLAY_SPEED=1.0` reproduces recorded elapsed time; values such as `2.0` play it twice as fast.

## Live connection behavior

The OPC UA reader runs in a background thread and keeps the last good telemetry snapshot. If the connection drops, the dashboard reports **STALE / LAST KNOWN** data instead of pretending that the values are live. If no good snapshot exists, it reports **OFFLINE**.

The diagnostics panel shows endpoint, namespace, received values, good/bad counts, and failed tags.

## Emergency behavior

The dashboard is read-only. It does not reset, start, stop, or command the factory.

When `LocalVariables.bEmergencyShutdown_Memory` is true, or when the exposed emergency input `bEmergencyShutdown_NotPressed` is false, the dashboard shows a prominent emergency banner and marks the factory regions red. The physical emergency stop and PLC safety logic remain authoritative.

## Virtual factory

The supplied factory photograph is the background. Transparent polygons highlight HBW, crane, machining station, punching machine, and sorting line. Region state is derived from actuator/sensor telemetry. Polygon coordinates are intentionally centralized in `visualization.py` so they can be calibrated against a better straight-on factory photograph.

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
- emergency visualization
- stale-data behavior
- simulation cycle/phase/color behavior
- timestamp-aware replay progression and looping
- live telemetry recording and duplicate suppression
- region activity and MS process phase interpretation
