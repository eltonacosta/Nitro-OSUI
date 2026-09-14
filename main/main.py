#!/usr/bin/env python3
import sys
import subprocess
import time

import fan_curve
import nitro_core as core


def cls():
    subprocess.run(["clear"])


def explain(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "Permission denied. Run nitroctl as root (sudo)."
    return str(exc)


def chk_os():
    if not core.is_linux():
        print(f"ERROR: Fatal: detected OS: {sys.platform}! nitroctl is built exclusively for Linux and will not function on any other OS. Exiting.", file=sys.stderr)
        sys.exit(1)


def chk_su():
    # Com a regra udev o usuário é dono dos nós do driver e escreve sem elevar;
    # root continua valendo. Só aborta quando nenhum dos dois é possível.
    if not core.can_control():
        print(
            "ERROR: Fatal: no write access to the driver. Run "
            "'./setup/install.sh --driver-only' once (it asks for your password) "
            "or run nitroctl as root. Exiting.",
            file=sys.stderr,
        )
        sys.exit(1)


def chk_driver():
    base = core.driver_base()
    if base is None:
        print(f"ERROR: Fatal: Driver directory '{core.SENSE_BASES[0]}' does not exist! Device might not be Acer. Exiting.", file=sys.stderr)
        sys.exit(1)
    if not core.features():
        print("ERROR: Fatal: nitro_sense directory not found in linuwu_sense directory. linuwu_sense might not be properly installed. Exiting.", file=sys.stderr)
        sys.exit(1)


def report(result, verb):
    done, skipped = result
    for name in done:
        print(f"{verb} {name}")
    for name, reason in skipped:
        print(f"Skipped {name}: {reason}")


def ask(prompt):
    """input() que trata Ctrl+C e Ctrl+D como cancelamento."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def thermal_profile_menu():
    cls()
    try:
        profiles = core.thermal_profiles()
    except OSError as exc:
        print(f"ERROR: Could not read available thermal modes: {exc}")
        time.sleep(2)
        return
    current = core.current_thermal_profile()

    print("Available thermal modes:")
    for idx, (mode, label) in enumerate(profiles, start=1):
        marker = " (current)" if mode == current else ""
        print(f"{idx}: {label}{marker}")

    raw = ask("Type the number of the mode you want to apply, then press enter.\n")
    if raw is None:
        print("Cancelled. Returning to main menu.")
        time.sleep(1)
        return
    thermal_profile = raw.strip()

    if thermal_profile.isdigit():
        idx = int(thermal_profile) - 1
        if 0 <= idx < len(profiles):
            try:
                core.set_thermal_profile(profiles[idx][0])
                print(f"Thermal mode set to {profiles[idx][1]}.")
            except OSError as exc:
                print(f"ERROR: {explain(exc)}")
            time.sleep(1)
        else:
            print("Invalid selection. Returning to main menu.")
            time.sleep(1)
    else:
        print("Invalid selection. Returning to main menu.")
        time.sleep(1)


def toggle_menu(attr, title, on_label="Enabled", off_label="Disabled"):
    """Menu de liga/desliga compartilhado por RGB timeout, bateria e LCD."""
    cls()
    if not core.supports(attr):
        print(f"ERROR: This device does not expose '{attr}'. Feature unavailable.")
        time.sleep(2)
        return
    print(f"Do you want to enable or disable {title}?")
    state = core.read_flag(attr)
    if state is True:
        print(f"Current state is: {on_label}\n")
    elif state is False:
        print(f"Current state is: {off_label}\n")
    else:
        print("Current state unknown.\n")
    print(f"1: {on_label}")
    print(f"2: {off_label}")
    raw = ask("Type the number of your choice, then press enter.\n")
    if raw is None:
        print("Cancelled. Returning to main menu.")
        time.sleep(1)
        return
    choice = raw.strip()
    if choice in ("1", "2"):
        try:
            core.set_flag(attr, choice == "1")
            print(f"{title} {'enabled' if choice == '1' else 'disabled'}.")
        except OSError as exc:
            print(f"ERROR: {explain(exc)}")
        time.sleep(1)
    else:
        print("Unknown state. Returning to main menu.")
        time.sleep(1)


def ask_fan_speed(device):
    print(f"Do you want manual or automatic {device} fan control?")
    print("1: Automatic")
    print("2: Manual")
    raw = ask("Type the number of your choice, then press enter.\n")
    if raw is None:
        print("Cancelled. Returning to main menu.")
        time.sleep(1)
        return None
    choice = raw.strip()
    if choice == "1":
        print(f"Automatic {device} fan control selected.\n")
        return core.FAN_AUTO
    if choice != "2":
        print("Unknown state. Returning to main menu.")
        time.sleep(1)
        return None
    speed = ask(
        f"Manual {device} fan control selected. Type your desired fan speed. "
        f"(valid range: {core.FAN_MIN}-{core.FAN_MAX}, {core.FAN_MIN} being the lowest and {core.FAN_MAX} being max.)\n "
    )
    if speed is None:
        print("Cancelled. Returning to main menu.")
        time.sleep(1)
        return None
    speed = speed.strip()
    if not core.valid_fan_speed(speed) or int(speed) == core.FAN_AUTO:
        print(f"Invalid input. Please enter a number between {core.FAN_MIN} and {core.FAN_MAX}. Returning to main menu.\n")
        time.sleep(1)
        return None
    return int(speed)


def fan_speed_menu():
    cls()
    if not core.supports("fan_speed"):
        print("ERROR: This device does not expose 'fan_speed'. Feature unavailable.")
        time.sleep(2)
        return
    print("Fan speed control")
    cpu_speed = ask_fan_speed("CPU")
    if cpu_speed is None:
        return
    gpu_speed = ask_fan_speed("GPU")
    if gpu_speed is None:
        return

    print("Target speeds are:")
    print(f"CPU Speed: {cpu_speed}")
    print(f"GPU Speed: {gpu_speed}")
    raw_confirm = ask("Is this correct? y/n")
    if raw_confirm is None:
        print("Returning to main menu.")
        time.sleep(1)
        return
    confirm = raw_confirm.strip().lower()
    if confirm != "y":
        print("Returning to main menu.")
        time.sleep(1)
        return
    try:
        core.set_fan_speed(cpu_speed, gpu_speed)
        print("Fan speeds applied.")
    except (OSError, ValueError) as exc:
        print(f"ERROR: {explain(exc)}")
    time.sleep(1)


def _parse_int(raw, low, high):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if low <= value <= high else None


def _save_curve(config):
    try:
        fan_curve.save_config(config)
        print(f"Curve saved to {fan_curve.config_path()}.")
    except (fan_curve.CurveConfigError, OSError) as exc:
        print(f"ERROR: {exc}")
    time.sleep(1)


def _edit_curve_levels(config, device):
    cls()
    points = config.points(device)
    print(f"{device.upper()} curve: {fan_curve.LEVELS} levels")
    print(f"Temperatures must increase; speeds are {fan_curve.MIN_SPEED}-{fan_curve.MAX_SPEED}%.")
    print("Press enter on an empty answer to keep the current value.\n")

    updated = []
    previous_temp = None
    for index in range(fan_curve.LEVELS):
        old_temp, old_speed = points[index]
        temp_raw = ask(f"Level {index + 1} temperature in °C [{old_temp}]: ")
        if temp_raw is None:
            print("Cancelled. Nothing changed.")
            time.sleep(1)
            return
        temp_raw = temp_raw.strip()
        temp = old_temp if temp_raw == "" else _parse_int(temp_raw, fan_curve.MIN_TEMP, fan_curve.MAX_TEMP)
        if temp is None:
            print(f"Invalid temperature (range {fan_curve.MIN_TEMP}-{fan_curve.MAX_TEMP}). Nothing changed.")
            time.sleep(1)
            return
        if previous_temp is not None and temp <= previous_temp:
            print(f"Temperature must be greater than {previous_temp} °C. Nothing changed.")
            time.sleep(1)
            return
        speed_raw = ask(f"Level {index + 1} fan speed in % [{old_speed}]: ")
        if speed_raw is None:
            print("Cancelled. Nothing changed.")
            time.sleep(1)
            return
        speed_raw = speed_raw.strip()
        speed = old_speed if speed_raw == "" else _parse_int(speed_raw, fan_curve.MIN_SPEED, fan_curve.MAX_SPEED)
        if speed is None:
            print(f"Invalid speed (range {fan_curve.MIN_SPEED}-{fan_curve.MAX_SPEED}). Nothing changed.")
            time.sleep(1)
            return
        updated.append((temp, speed))
        previous_temp = temp

    config.replace_points(device, updated)
    _save_curve(config)


def _show_curve_status(config):
    cls()
    state_text = "enabled" if config.enabled else "disabled"
    print(f"Fan curve: {state_text}")
    print(f"GPU idle: {config.gpu_idle_speed}% | fail-safe: {config.fail_safe_speed}% | "
          f"hysteresis: {config.hysteresis_c} °C | min interval: {config.min_write_interval_s} s")
    for device in fan_curve.DEVICES:
        levels = " · ".join(f"{temp} °C->{speed}%" for temp, speed in config.points(device))
        print(f"{device.upper()}: {levels}")

    state = fan_curve.read_state()
    if state:
        age = fan_curve.state_age(state)
        age_text = f"{age:.0f} s ago" if age is not None else "age unknown"
        print(f"\nLast report ({state.get('source')}, {age_text}): {state.get('description') or '-'}")
        print(f"Applied: {state.get('applied') or 'auto (firmware)'}")
        if state.get("error"):
            print(f"Error: {state['error']}")
    else:
        print("\nNo report yet: the background service (nitroctl-curve) has not run.")
    ask("Press enter to continue.\n")


def fan_curve_menu():
    try:
        config = fan_curve.load_config()
    except fan_curve.CurveConfigError as exc:
        print(f"ERROR: saved curve is invalid ({exc}); starting from defaults.")
        time.sleep(2)
        config = fan_curve.default_config()

    while True:
        cls()
        print("Fan curve")
        print(f"1: Turn the curve {'off' if config.enabled else 'on'} (currently {'on' if config.enabled else 'off'})")
        print("2: Edit CPU levels")
        print("3: Edit GPU levels")
        print("4: Show status")
        print("5: Apply once now (foreground test)")
        print("6: Reset to defaults")
        print("B: Back to main menu")
        choice = ask("Type the number of your choice, then press enter.\n")
        if choice is None:
            return
        choice = choice.strip().lower()

        if choice == "b":
            return
        if choice == "1":
            config.enabled = not config.enabled
            _save_curve(config)
        elif choice == "2":
            _edit_curve_levels(config, "cpu")
        elif choice == "3":
            _edit_curve_levels(config, "gpu")
        elif choice == "4":
            _show_curve_status(config)
        elif choice == "5":
            cls()
            engine = fan_curve.CurveEngine(config)
            error = engine.tick()
            outcome = engine.last_outcome
            if outcome is not None:
                print(outcome.describe())
            if error:
                print(f"ERROR: {error}")
            elif engine.last_applied is None:
                print("Nothing applied (value already in effect or minimum interval).")
            fan_curve.write_state(outcome, engine.last_applied, error, source="cli")
            time.sleep(2)
        elif choice == "6":
            config.curves = fan_curve.default_curves()
            _save_curve(config)
        else:
            print("Unknown option. Returning to the curve menu.")
            time.sleep(1)


def mainloop():
    cls()
    print("Welcome to nitroctl!")
    print("Choose one of the following to continue:")
    print("1: Thermal Profile")
    print("2: Keyboard RGB Timeout")
    print("3: Battery Limiter")
    print("4: Fan Speed")
    print("5: LCD Overdrive")
    print("6: Keyboard RGB Configuration [UNIMPLEMENTED]")
    print("7: Fan Curve")
    print("S: Save current configuration")
    print("L: Load configuration from default path [UNTESTED, MIGHT BREAK YOUR SYSTEM!]")
    print("Q: Quit program")
    choice = ask("Type the number of your choice, then press enter.\n")
    if choice is None:
        return "q"
    return choice.strip()


def run():
    chk_os()
    chk_su()
    chk_driver()

    while True:
        next_function = mainloop()
        choice = next_function.lower()

        if choice == "q":
            cls()
            print("Exiting. Goodbye!")
            break
        elif choice == "s":
            print("Saving configuration to default directory: ~/.config/nitroctl")
            try:
                report(core.save_config(), "Copied")
            except OSError as exc:
                print(f"ERROR: {explain(exc)}")
            time.sleep(1)
        elif next_function == "1":
            thermal_profile_menu()
        elif next_function == "2":
            toggle_menu("backlight_timeout", "keyboard RGB timeout")
        elif next_function == "3":
            toggle_menu("battery_limiter", "battery limiter")
        elif next_function == "4":
            fan_speed_menu()
        elif next_function == "5":
            toggle_menu("lcd_override", "LCD Overdrive")
        elif next_function == "6":
            cls()
            print("Keyboard RGB configuration is not implemented by the nitroctl project yet.")
            time.sleep(2)
        elif next_function == "7":
            fan_curve_menu()
        elif choice == "l":
            print(f"Loading previous configuration from {core.config_dir()}")
            try:
                report(core.load_config(), "Loaded")
                print("Loading finished.")
            except OSError as exc:
                print(f"ERROR: {explain(exc)}")
            time.sleep(1)
        else:
            print("Unknown option. Returning to main menu.")
            time.sleep(1)


if __name__ == "__main__":
    run()