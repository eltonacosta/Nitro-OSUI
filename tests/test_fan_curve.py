#!/usr/bin/env python3
"""Testes do motor da curva de ventoinha.

Roda sem tocar no hardware: os sensores e a escrita no sysfs são substituídos
por dublês. Uso:

    python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "main"))

import fan_curve as fc  # noqa: E402
import nitro_core as core  # noqa: E402


class FakeReading:
    def __init__(self, key, value):
        self.key = key
        self.value = value


def readings(cpu_temp, gpu_temp):
    """Réplica mínima do que sensor_readings() devolveria."""
    return [
        FakeReading("cpu_temp", cpu_temp),
        FakeReading("gpu_temp", gpu_temp),
        FakeReading("board_temp", 45.0),
        FakeReading("cpu_fan", 3000),
        FakeReading("gpu_fan", 2500),
    ]


class InterpolationTests(unittest.TestCase):
    def setUp(self):
        self.points = fc.default_config().points("cpu")

    def test_below_first_level_uses_first_speed(self):
        self.assertEqual(fc.speed_for(5, self.points), 20)

    def test_above_last_level_uses_last_speed(self):
        self.assertEqual(fc.speed_for(200, self.points), 100)

    def test_exact_levels(self):
        for temp, speed in self.points:
            self.assertEqual(fc.speed_for(temp, self.points), speed)

    def test_linear_midpoint(self):
        # 45 °C fica entre (40, 30) e (50, 40) -> 35%
        self.assertEqual(fc.speed_for(45, self.points), 35)

    def test_floor_of_20_is_never_broken(self):
        curve = [(30, 20), (40, 20), (50, 20), (60, 20), (70, 20), (80, 20), (90, 20)]
        for temp in range(-10, 120):
            self.assertGreaterEqual(fc.speed_for(temp, curve), fc.MIN_SPEED)

    def test_result_is_clamped_to_speed_range(self):
        curve = [(30, 100)] * 7
        for temp in (0, 50, 300):
            self.assertEqual(fc.speed_for(temp, curve), fc.MAX_SPEED)


class ValidationTests(unittest.TestCase):
    def test_default_config_is_valid(self):
        fc.validate(fc.default_config())

    def test_wrong_level_count_is_rejected(self):
        config = fc.default_config()
        config.replace_points("cpu", [(30, 20)] * 6)
        with self.assertRaises(fc.CurveConfigError):
            fc.validate(config)

    def test_non_increasing_temps_are_rejected(self):
        config = fc.default_config()
        config.replace_points("gpu", [(30, 20), (40, 20), (40, 30), (60, 30),
                                      (70, 30), (80, 30), (90, 30)])
        with self.assertRaises(fc.CurveConfigError):
            fc.validate(config)

    def test_speed_below_floor_is_rejected(self):
        config = fc.default_config()
        config.replace_points("cpu", [(30, 19), (40, 30), (50, 40), (60, 55),
                                      (70, 70), (80, 85), (90, 100)])
        with self.assertRaises(fc.CurveConfigError):
            fc.validate(config)

    def test_idle_and_fail_safe_below_floor_are_rejected(self):
        config = fc.default_config()
        config.gpu_idle_speed = 10
        with self.assertRaises(fc.CurveConfigError):
            fc.validate(config)


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.config = fc.default_config()

    def test_normal_case_follows_both_curves(self):
        outcome = fc.compute_outcome(self.config, 64, 47)
        self.assertEqual(outcome.cpu_speed, fc.speed_for(64, self.config.points("cpu")))
        self.assertEqual(outcome.gpu_speed, fc.speed_for(47, self.config.points("gpu")))
        self.assertEqual(outcome.notes, [])

    def test_gpu_without_reading_uses_idle_speed(self):
        outcome = fc.compute_outcome(self.config, 60, None)
        self.assertEqual(outcome.gpu_speed, fc.DEFAULT_GPU_IDLE_SPEED)
        self.assertIn("gpu-idle", outcome.notes)

    def test_cpu_failure_goes_fail_safe_on_both(self):
        outcome = fc.compute_outcome(self.config, None, 47)
        self.assertEqual(outcome.cpu_speed, fc.DEFAULT_FAIL_SAFE_SPEED)
        self.assertEqual(outcome.gpu_speed, fc.DEFAULT_FAIL_SAFE_SPEED)

    def test_total_failure_goes_fail_safe(self):
        outcome = fc.compute_outcome(self.config, None, None)
        self.assertEqual(outcome.pair, (fc.DEFAULT_FAIL_SAFE_SPEED, fc.DEFAULT_FAIL_SAFE_SPEED))


class EngineTests(unittest.TestCase):
    """Motor com sensores e escrita dublês: nada toca no sysfs."""

    def setUp(self):
        self.config = fc.default_config()
        self.config.min_write_interval_s = 5
        self.written = []
        self.now = 1000.0
        self.temps = (60.0, 50.0)

    def make_engine(self, temps=None):
        state = {"temps": temps if temps is not None else self.temps}

        def reader():
            return readings(*state["temps"])

        def applier(cpu, gpu):
            self.written.append((cpu, gpu))

        engine = fc.CurveEngine(self.config, reader=reader, applier=applier,
                                clock=lambda: self.now)
        return engine, state

    def test_first_tick_applies_and_records(self):
        engine, _ = self.make_engine()
        self.assertIsNone(engine.tick())
        self.assertEqual(len(self.written), 1)
        cpu_speed = fc.speed_for(60.0, self.config.points("cpu"))
        gpu_speed = fc.speed_for(50.0, self.config.points("gpu"))
        self.assertEqual(self.written[0], (cpu_speed, gpu_speed))

    def test_unchanged_temperature_does_not_rewrite(self):
        engine, _ = self.make_engine()
        engine.tick()
        engine.tick()
        self.assertEqual(len(self.written), 1)

    def test_min_write_interval_blocks_rapid_changes(self):
        engine, state = self.make_engine()
        engine.tick()
        state["temps"] = (80.0, 50.0)  # subiu bastante
        engine.tick()                  # tempo não passou: não deve escrever
        self.assertEqual(len(self.written), 1)
        self.now += 5.0
        engine.tick()
        self.assertEqual(len(self.written), 2)

    def test_hysteresis_holds_descent_until_temperature_drops(self):
        engine, state = self.make_engine(temps=(80.0, 50.0))
        engine.tick()
        first = self.written[-1][0]
        # Cai 1 °C (dentro da histerese de 2 °C): mantém a velocidade.
        state["temps"] = (79.0, 50.0)
        self.now += 10.0
        engine.tick()
        self.assertEqual(self.written[-1][0], first)
        # Cai bem abaixo do limiar: agora sim reduz.
        state["temps"] = (74.0, 50.0)
        self.now += 10.0
        engine.tick()
        self.assertLess(self.written[-1][0], first)

    def test_rise_is_never_held_back(self):
        engine, state = self.make_engine(temps=(50.0, 50.0))
        engine.tick()
        first = self.written[-1][0]
        state["temps"] = (70.0, 50.0)
        self.now += 10.0
        engine.tick()
        self.assertGreater(self.written[-1][0], first)

    def test_gpu_idle_applies_idle_speed(self):
        engine, _ = self.make_engine(temps=(60.0, None))
        engine.tick()
        self.assertEqual(self.written[-1][1], fc.DEFAULT_GPU_IDLE_SPEED)

    def test_cpu_failure_applies_fail_safe(self):
        engine, _ = self.make_engine(temps=(None, None))
        engine.tick()
        self.assertEqual(self.written[-1], (fc.DEFAULT_FAIL_SAFE_SPEED,) * 2)

    def test_restore_auto_writes_zero(self):
        engine, _ = self.make_engine()
        engine.tick()
        self.assertIsNone(engine.restore_auto())
        self.assertEqual(self.written[-1], (core.FAN_AUTO, core.FAN_AUTO))

    def test_missing_fan_attribute_reports_error(self):
        engine, _ = self.make_engine()
        original = core.supports
        core.supports = lambda attr: False
        try:
            error = engine.tick()
        finally:
            core.supports = original
        self.assertIsNotNone(error)
        self.assertEqual(self.written, [])

    def test_write_failure_is_reported_not_raised(self):
        def broken(cpu, gpu):
            raise PermissionError("permissão negada")

        engine = fc.CurveEngine(self.config, reader=lambda: readings(60.0, 50.0),
                                applier=broken, clock=lambda: self.now)
        error = engine.tick()
        self.assertIsNotNone(error)
        self.assertEqual(engine.last_applied, None)


class ConfigFileTests(unittest.TestCase):
    def test_save_load_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "curves" / "fan_curve.json"
            config = fc.default_config()
            config.enabled = True
            config.replace_points("gpu", [(31, 21), (41, 31), (51, 41),
                                          (61, 56), (71, 71), (81, 86), (91, 100)])
            fc.save_config(config, path)
            loaded = fc.load_config(path)
            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.points("gpu"), config.points("gpu"))
            self.assertEqual(loaded.gpu_idle_speed, config.gpu_idle_speed)

    def test_missing_file_gives_defaults(self):
        loaded = fc.load_config(Path("/nonexistent/fan_curve.json"))
        self.assertFalse(loaded.enabled)
        self.assertEqual(len(loaded.points("cpu")), fc.LEVELS)


class LockTests(unittest.TestCase):
    def test_second_lock_is_denied_until_release(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "curve.lock"
            first, second = fc.CurveLock(path), fc.CurveLock(path)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()


if __name__ == "__main__":
    unittest.main()