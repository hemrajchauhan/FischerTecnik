from factory_dashboard.simulation import FactorySimulation
from factory_dashboard.logic import color_class


def test_simulation_cycle_is_deterministic_from_elapsed_time():
    sim = FactorySimulation()
    sim.t0 = sim.t0.replace(microsecond=0)
    sim.offset = sum(d for _, d in sim.PHASES) * 2.0 + 0.5
    sim.running = False
    snap = sim.snapshot()
    assert snap.get("sim.cycle") == 2


def test_simulation_exposes_color_value_and_class():
    sim = FactorySimulation()
    snap = sim.snapshot()
    assert snap.get("sl.sensor.color_value") in {50, 150, 250}
    assert snap.get("sim.color") == color_class(snap.get("sl.sensor.color_value"))


def test_simulation_has_burning_phase():
    sim = FactorySimulation()
    sim.offset = sum(d for _, d in sim.PHASES[:4]) + 0.5
    sim.running = False
    snap = sim.snapshot()
    assert snap.get("sim.phase") == "Burning"
    assert snap.bool("ms.process.burn")
