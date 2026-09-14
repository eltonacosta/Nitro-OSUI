#!/usr/bin/env python3
"""Testes da integração da curva na GUI, sem display.

O GTK é substituído por dublês mínimos (widgets que aceitam qualquer método e
registram o que importa), o sysfs é substituído por valores de teste e a
escrita no driver por um espião. Assim a lógica da janela — validação dos 7
níveis, travas dos controles manuais, aplicação e restauração — roda de fato.

    python3 -m unittest discover -s tests -v
"""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "main"))


# --------------------------------------------------------------- dublê do GTK
class FakeBase:
    """Widget genérico: set_*/get_* viram propriedades, o resto é no-op."""

    def __init__(self, *args, **kwargs):
        object.__setattr__(self, "_props", dict(kwargs))
        object.__setattr__(self, "_handlers", {})
        object.__setattr__(self, "_blocked", False)

    # construtores usados pelo gtk_app
    @classmethod
    def new_with_range(cls, lower, upper, step):
        widget = cls()
        widget._props["value"] = lower
        widget._props["lower"] = lower
        widget._props["upper"] = upper
        return widget

    @classmethod
    def new_with_label(cls, text):
        return cls(label=text)

    @classmethod
    def new_from_icon_name(cls, name):
        return cls(icon=name)

    @classmethod
    def new(cls, *args, **kwargs):
        return cls(*args, **kwargs)

    # sinais
    def connect(self, signal, handler, *extra):
        self._handlers.setdefault(signal, []).append((handler, extra))
        return len(self._handlers[signal])

    def emit(self, signal, *args):
        """Reproduz a assinatura do GTK: notify::* passa um pspec antes do
        user_data; os demais sinais passam só o widget e o user_data."""
        if self._blocked:
            return
        notify = signal.startswith("notify::")
        for handler, extra in list(self._handlers.get(signal, [])):
            if notify:
                handler(self, None, *extra)
            else:
                handler(self, *extra)

    def handler_block_by_func(self, _handler):
        object.__setattr__(self, "_blocked", True)

    def handler_unblock_by_func(self, _handler):
        object.__setattr__(self, "_blocked", False)

    # estado
    def set_active(self, value):
        self._props["active"] = value

    def get_active(self):
        return self._props.get("active", False)

    def set_value(self, value):
        self._props["value"] = value

    def get_value(self):
        return self._props.get("value", 0)

    def set_selected(self, value):
        self._props["selected"] = value

    def get_selected(self):
        return self._props.get("selected", 0)

    def set_model(self, model):
        self._props["model"] = model

    def get_n_items(self):
        return len(self._props.get("model") or [])

    def get_subtitle(self):
        return self._props.get("subtitle")

    def get_title(self):
        return self._props.get("title")

    def get_sensitive(self):
        return self._props.get("sensitive", True)

    def splice(self, _position, _count, items):
        self._props["model"] = list(items)

    def __getattr__(self, name):
        if name.startswith("set_"):
            key = name[4:]
            return lambda value=None, *a, **k: self._props.__setitem__(key, value)
        if name.startswith("get_"):
            key = name[4:]
            return lambda *a, **k: self._props.get(key)
        if name.startswith("add_") or name in ("append", "pack_end", "pack_start"):
            def adder(child, *a, **k):
                self._props.setdefault("children", []).append(child)
            return adder
        return lambda *a, **k: None


class AutoModule:
    """Módulo falso: atributo ausente vira uma classe de widget sob demanda."""

    def __init__(self, **values):
        self.__dict__.update(values)
        self.__dict__["_cache"] = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        cache = self.__dict__["_cache"]
        if name not in cache:
            cache[name] = type(name, (FakeBase,), {})
        return cache[name]


class FakeApplication(FakeBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.props = types.SimpleNamespace(active_window=None)

    def run(self, _argv):
        return 0


class FakeAlertDialog(FakeBase):
    def choose(self, *_args):
        return None


def _install_fake_gi():
    gi = types.ModuleType("gi")
    gi.require_version = lambda *a, **k: None

    repository = types.ModuleType("gi.repository")
    timers = []

    def timeout_add(milliseconds, callback):
        timers.append((milliseconds, callback))
        return len(timers)

    gtk = AutoModule(INVALID_LIST_POSITION=-1,
                     Orientation=types.SimpleNamespace(VERTICAL=1, HORIZONTAL=0))
    adw = AutoModule(Application=FakeApplication, AlertDialog=FakeAlertDialog,
                     ToastPriority=types.SimpleNamespace(HIGH=1, NORMAL=0),
                     ResponseAppearance=types.SimpleNamespace(DESTRUCTIVE=1))
    gio = AutoModule(ApplicationFlags=types.SimpleNamespace(DEFAULT_FLAGS=0),
                     AsyncResult=object)
    glib = AutoModule(timeout_add=timeout_add, timers=timers)

    repository.Gtk = gtk
    repository.Adw = adw
    repository.Gio = gio
    repository.GLib = glib
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository
    return gtk, adw, glib


GTK, ADW, GLIB = _install_fake_gi()

import fan_curve as fc  # noqa: E402
import gtk_app  # noqa: E402
import nitro_core as core  # noqa: E402


def reading(key, label, kind, value, unit):
    """Leitura real do core (mesma dataclass que a GUI consome)."""
    return core.SensorReading(key=key, label=label, kind=kind, value=value, unit=unit)


class GuiCurveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)

        self.sensor_values = {"cpu_temp": 60.0, "gpu_temp": 50.0}
        self.applied = []
        self.toasts = []

        patches = {
            "user_home": lambda: home,
            "can_control": lambda: True,
            "supports": lambda _attr: True,
            "thermal_profiles": lambda: [("balanced", "Balanced")],
            "current_thermal_profile": lambda: "balanced",
            "fan_speed": lambda: (0, 0),
            "read_flag": lambda _attr: False,
            "model_name": lambda: "nitro_sense",
            "features": lambda: ["fan_speed"],
            "driver_base": lambda: Path("/sys/devices/platform/acer-wmi"),
            "sensor_readings": self._readings,
            "set_fan_speed": self._apply,
        }
        for name, value in patches.items():
            patcher = mock.patch.object(core, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.window = gtk_app.NitroWindow(app=ADW.Application())
        self.window.notify = lambda message, error=False: self.toasts.append((message, error))

    def _readings(self):
        return [
            reading("cpu_temp", "CPU temperature", "temp", self.sensor_values["cpu_temp"], "°C"),
            reading("gpu_temp", "GPU temperature", "temp", self.sensor_values["gpu_temp"], "°C"),
            reading("board_temp", "Board temperature", "temp", 45.0, "°C"),
            reading("cpu_fan", "CPU fan", "fan", 3000, "RPM"),
            reading("gpu_fan", "GPU fan", "fan", 2500, "RPM"),
        ]

    def _apply(self, cpu, gpu):
        self.applied.append((cpu, gpu))

    def _enable_curve(self):
        self.window.curve_switch.set_active(True)
        self.window.curve_switch.emit("notify::active")

    # ------------------------------------------------------------- construção
    def test_seven_levels_per_device(self):
        self.assertEqual(len(self.window.curve_rows["cpu"]), fc.LEVELS)
        self.assertEqual(len(self.window.curve_rows["gpu"]), fc.LEVELS)

    def test_spin_widgets_start_on_saved_values(self):
        for device in fc.DEVICES:
            spins = self.window.curve_rows[device]
            for (temp_spin, speed_spin), (temp, speed) in zip(spins, fc.default_config().points(device)):
                self.assertEqual(temp_spin.get_value(), temp)
                self.assertEqual(speed_spin.get_value(), speed)

    def test_speed_spin_cannot_go_below_floor(self):
        _temp_spin, speed_spin = self.window.curve_rows["cpu"][0]
        self.assertEqual(speed_spin._props["lower"], fc.MIN_SPEED)

    def test_curve_timer_is_registered(self):
        intervals = [ms for ms, _cb in GLIB.timers]
        self.assertIn(fc.TICK_SECONDS * 1000, intervals)

    # ---------------------------------------------------------------- edição
    def test_edit_persists_to_config_file(self):
        temp_spin, speed_spin = self.window.curve_rows["cpu"][2]
        temp_spin.set_value(52)
        speed_spin.set_value(45)
        temp_spin.emit("value-changed")
        saved = fc.load_config()
        self.assertEqual(saved.points("cpu")[2], (52, 45))

    def test_non_increasing_temperatures_are_reverted(self):
        first, second = self.window.curve_rows["cpu"][0], self.window.curve_rows["cpu"][1]
        second[0].set_value(30)  # igual à temperatura do nível 1
        second[0].emit("value-changed")
        self.assertTrue(any("increasing" in message for message, _error in self.toasts))
        # valores voltaram ao que estava salvo
        self.assertEqual(second[0].get_value(), fc.DEFAULT_TEMPS[1])

    def test_reset_restores_defaults(self):
        temp_spin, speed_spin = self.window.curve_rows["gpu"][0]
        temp_spin.set_value(35)
        speed_spin.set_value(60)
        temp_spin.emit("value-changed")
        self.window._on_curve_reset(GTK.Widget())
        self.assertEqual(fc.load_config().points("gpu"), list(zip(fc.DEFAULT_TEMPS, fc.DEFAULT_SPEEDS)))

    # -------------------------------------------------------------- aplicação
    def test_enabling_curve_locks_manual_controls(self):
        self._enable_curve()
        auto_row, speed_row = self.window.fan_rows["CPU"]
        self.assertFalse(auto_row.get_sensitive())
        self.assertFalse(speed_row.get_sensitive())
        self.assertFalse(self.window.apply_button.get_sensitive())

    def test_disabling_curve_restores_manual_controls(self):
        self._enable_curve()
        self.window.curve_switch.set_active(False)
        self.window.curve_switch.emit("notify::active")
        auto_row, _speed_row = self.window.fan_rows["CPU"]
        self.assertTrue(auto_row.get_sensitive())
        self.assertTrue(self.window.apply_button.get_sensitive())

    def test_tick_applies_curve_when_window_owns_the_lock(self):
        self._enable_curve()
        self.window._curve_tick()
        cpu_speed = fc.speed_for(60.0, fc.default_config().points("cpu"))
        gpu_speed = fc.speed_for(50.0, fc.default_config().points("gpu"))
        self.assertEqual(self.applied, [(cpu_speed, gpu_speed)])

    def test_tick_does_nothing_while_disabled(self):
        self.window._curve_tick()
        self.assertEqual(self.applied, [])

    def test_gpu_idle_uses_idle_speed(self):
        self._enable_curve()
        self.sensor_values["gpu_temp"] = None
        self.window._curve_tick()
        self.assertEqual(self.applied[-1][1], fc.DEFAULT_GPU_IDLE_SPEED)

    def test_disabling_after_applying_restores_auto(self):
        self._enable_curve()
        self.window._curve_tick()
        self.window.curve_switch.set_active(False)
        self.window.curve_switch.emit("notify::active")
        self.assertEqual(self.applied[-1], (core.FAN_AUTO, core.FAN_AUTO))

    def test_closing_window_restores_auto_when_applying(self):
        self._enable_curve()
        self.window._curve_tick()
        self.assertFalse(self.window._on_close_request(GTK.Window()))
        self.assertEqual(self.applied[-1], (core.FAN_AUTO, core.FAN_AUTO))

    # ------------------------------------------------- falhas e recuperação
    def test_failed_save_reverts_the_switch(self):
        """Config root-owned: o switch não pode ficar ligado se não persistiu."""
        with mock.patch.object(fc, "save_config",
                               side_effect=fc.CurveConfigError("sem permissão")):
            self._enable_curve()
        self.assertFalse(self.window.curve_switch.get_active())
        self.assertFalse(self.window.curve_config.enabled)
        self.assertTrue(any("save the fan curve" in m for m, _e in self.toasts))

    def test_failed_save_reverts_edited_levels(self):
        with mock.patch.object(fc, "save_config",
                               side_effect=fc.CurveConfigError("sem permissão")):
            temp_spin, _speed = self.window.curve_rows["cpu"][2]
            temp_spin.set_value(52)
            temp_spin.emit("value-changed")
        self.assertEqual(temp_spin.get_value(), fc.DEFAULT_TEMPS[2])

    def test_turning_curve_off_restores_firmware_auto_in_the_ui(self):
        self._enable_curve()
        self.window._curve_tick()
        self.window.curve_switch.set_active(False)
        self.window.curve_switch.emit("notify::active")
        # fan_speed volta (0,0) => as duas linhas "automatic" ficam ligadas
        auto_row, _speed_row = self.window.fan_rows["CPU"]
        self.assertTrue(auto_row.get_active())
        self.assertTrue(auto_row.get_sensitive())

    def test_manual_rows_explain_why_they_are_locked(self):
        self._enable_curve()
        auto_row, _speed_row = self.window.fan_rows["CPU"]
        self.assertIn("Fan curve is active", auto_row.get_subtitle())

    def test_manual_rows_get_their_note_back_when_curve_is_off(self):
        self._enable_curve()
        self.window.curve_switch.set_active(False)
        self.window.curve_switch.emit("notify::active")
        auto_row, _speed_row = self.window.fan_rows["CPU"]
        self.assertNotIn("Fan curve is active", auto_row.get_subtitle() or "")

    def test_driver_coming_back_refreshes_the_status(self):
        original = core.driver_base
        with mock.patch.object(core, "driver_base", lambda: None):
            self.window._poll_sensors()
        calls = []
        with mock.patch.object(core, "driver_base",
                               lambda: Path("/sys/devices/platform/acer-wmi")):
            self.window.refresh = lambda: calls.append("refresh")
            self.window._poll_sensors()
        core.driver_base = original
        self.assertEqual(calls, ["refresh"])

    def test_status_row_reports_disabled(self):
        self.window._update_curve_status()
        self.assertIn("disabled", self.window.curve_status_row.get_subtitle())

    def test_status_row_reports_live_values_after_tick(self):
        self._enable_curve()
        self.window._curve_tick()
        subtitle = self.window.curve_status_row.get_subtitle()
        self.assertIn("CPU", subtitle)
        self.assertIn("applied by this window", subtitle)


if __name__ == "__main__":
    unittest.main()