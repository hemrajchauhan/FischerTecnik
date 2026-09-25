from factory_dashboard.simulation import FactorySimulation
from factory_dashboard.logic import color_class
from factory_dashboard.process_model import REFERENCE_CYCLE_SECONDS


def test_simulation_cycle_is_deterministic_from_measured_cycle_time():
    sim = FactorySimulation()
    sim.offset = REFERENCE_CYCLE_SECONDS * 2.0 + 0.5
    sim.running = False
    snap = sim.snapshot()
    assert snap.get("sim.cycle") == 3
    assert snap.get("sim.cycle_period_s") == REFERENCE_CYCLE_SECONDS


def test_simulation_exposes_color_value_and_class():
    sim = FactorySimulation()
    snap = sim.snapshot()
    assert snap.get("sl.sensor.color_value") in {50, 150, 250}
    assert snap.get("sim.color") == color_class(snap.get("sl.sensor.color_value"))


def test_simulation_has_burning_phase_at_cycle_start():
    sim = FactorySimulation()
    sim.offset = 0.5
    sim.running = False
    snap = sim.snapshot()
    assert snap.get("sim.phase") == "Burning"
    assert snap.bool("ms.process.burn")
