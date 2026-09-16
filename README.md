# Nitro-OSUI (nitroctl)

CLI + GTK4 GUI to control Acer Nitro laptops on Linux: keyboard RGB, thermal profiles, battery limiter and fan curves, through the [Linuwu-Sense](https://github.com/0x7375646F/Linuwu-Sense) driver. Nitro devices only; distro-agnostic, no systemd dependency. Tested on Void Linux and CachyOS.

## Install

```bash
git clone https://github.com/eltonacosta/Nitro-OSUI.git && cd Nitro-OSUI && ./setup/install.sh
```

The installer detects your distro, installs Python, git, kernel headers, the GUI bindings and the driver (via DKMS, rebuilt automatically on kernel updates). Non-interactive: add `--yes`. Preview without changes: `--dry-run --verbose`.

```bash
./setup/install.sh --no-gui          # CLI only
./setup/install.sh --no-driver       # skip the Linuwu-Sense DKMS step
./setup/install.sh --driver-only     # only (re)install the driver
./setup/install.sh --no-autostart    # skip the fan-curve autostart entry
./setup/install.sh --uninstall       # remove nitroctl + driver
```

Prefer manual? Install [Linuwu-Sense](https://github.com/0x7375646F/Linuwu-Sense), clone this repo and run `./nitroctl.sh` (needs only python3 + git).

Works on Arch, CachyOS, Debian, Ubuntu, Fedora, openSUSE, Void, Alpine and Gentoo; anything else gets generic instructions. Clang-built kernels (like CachyOS) are compiled with LLVM automatically.

After installing you get three commands:

* `nitroctl` — text menu
* `nitroctl-gui` — native GTK4 + Libadwaita window, same features, no password needed (the installer adds a udev rule giving your user access to the driver's sysfs controls)
* `nitroctl-curve` — fan curve daemon (see below)

## Fan curve

The firmware only offers "automatic" or a fixed speed, so nitroctl applies a curve from userspace: **7 levels per fan** (CPU/GPU), each with editable temperature and speed, interpolated linearly. Edit in the GUI (Fan curve card) or CLI (menu item `7`) — changes apply immediately.

Safety rules: never below 20%; suspended GPU holds that fan at 40% idle; failed sensor ramps both fans to 75%; writes only on change, at most every 5 s, with 2 °C hysteresis.

The curve keeps running with the GUI closed via `nitroctl-curve`, started with your session through XDG autostart (works on GNOME, KDE, Xfce, Sway — no systemd). Only one writer at a time; turning the curve off hands the fans back to the firmware.

```bash
nitroctl-curve --status        # show levels and last report
nitroctl-curve --once          # apply a single tick (diagnostics)
nitroctl-curve --restore-auto  # hand both fans back to the firmware
```

## Updating

```bash
nitroctl-update
```

Pulls the latest code and re-syncs the installed copy — no password, driver and dependencies untouched. If the driver itself changes, run `./setup/install.sh --driver-only`.

## To Do

* [❌] Keyboard RGB
* [❌] Temperatures and fan RPM in the CLI main menu
* [✅] GUI with live sensors (refreshed every 800 ms)
* [✅] Fan curve: 7 levels per fan, applied by a background daemon
* [✅] Config save/load, install guide

## Credits, origin, and AI

Based on the original [nitroctl repository by cani442k](https://github.com/cani442k/nitroctl) — thanks to its author and contributors for the foundation. This fork follows a different idea and direction, with its own goals, features, and design decisions.

AI tools are used in this project's development, and contributors are encouraged to use them to propose improvements and new features. Nothing is accepted automatically: AI-generated or AI-assisted changes land only after human review, testing, and validation for correctness, safety, maintainability, and compatibility.
