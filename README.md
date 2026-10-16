# Nitro-OSUI (nitroctl)

*OSUI = OpenSource UI.* CLI + GTK4 GUI to control Acer Nitro laptops on Linux: keyboard RGB, thermal profiles, battery limiter and fan curves, through the [Linuwu-Sense](https://github.com/0x7375646F/Linuwu-Sense) driver. Nitro devices only; distro-agnostic, no systemd dependency. Tested on Void Linux and CachyOS.

## Install

```bash
git clone https://github.com/eltonacosta/Nitro-OSUI.git && cd Nitro-OSUI && ./setup/install.sh
```

The installer detects your distro, installs Python, git, kernel headers, the GUI bindings and the driver (via DKMS, rebuilt automatically on kernel updates). Non-interactive: add `--yes`. Preview without changes: `--dry-run --verbose`.

```bash
./setup/install.sh --no-gui          # CLI only
./setup/install.sh --no-driver       # skip the Linuwu-Sense DKMS step
./setup/install.sh --driver-only     # only (re)install the driver
./setup/install.sh --no-autostart    # skip the session autostart entries (fan curve + restore)
./setup/install.sh --uninstall       # remove nitroctl + driver
```

Prefer manual? Install [Linuwu-Sense](https://github.com/0x7375646F/Linuwu-Sense), clone this repo and run `./nitroctl.sh` (needs only python3 + git).

Works on Arch, CachyOS, Debian, Ubuntu, Fedora, openSUSE, Void, Alpine and Gentoo; anything else gets generic instructions. Clang-built kernels (like CachyOS) are compiled with LLVM automatically.

After installing you get four commands:

* `nitroctl` — text menu
* `nitroctl-gui` — native GTK4 + Libadwaita window, same features, no password needed (the installer adds a udev rule giving your user access to the driver's sysfs controls)
* `nitroctl-curve` — fan curve daemon (see below)
* `nitroctl-restore` — reapplies your saved configuration at login (autostart, see below)

### Install with an AI assistant

Copy the prompt below into your AI coding agent (Claude Code, opencode, Codex, Gemini CLI…) and it will check the requirements, preview the changes and walk through the install with you.

```text
Install nitroctl (https://github.com/eltonacosta/Nitro-OSUI) on this Linux machine.

nitroctl is a CLI + GTK4 GUI for Acer Nitro laptops: keyboard RGB, thermal
profiles, battery limiter and fan curves. It controls the hardware through the
Linuwu-Sense kernel driver, built from source and rebuilt on kernel updates
via DKMS.

1. Check the requirements; if any fails, stop and report instead of continuing:
   - Linux on an Acer Nitro/Predator: cat /sys/class/dmi/id/sys_vendor and /sys/class/dmi/id/product_name;
   - the user can run sudo (needed for packages, DKMS, udev and the system service);
   - internet access; the installer adds kernel headers when they are missing.
2. Clone the repository and read README.md and setup/install.sh before running anything:
   git clone https://github.com/eltonacosta/Nitro-OSUI.git && cd Nitro-OSUI
3. Show the user what the installer plans to do and ask for explicit approval:
   ./setup/install.sh --dry-run --verbose
   It installs distro packages (python3, git, kernel headers, GTK4 bindings,
   DKMS, and the Clang toolchain when the kernel needs it), registers the
   Linuwu-Sense driver in DKMS, adds a udev rule for passwordless control and
   installs the fan-curve and session-restore autostart entries.
4. Run it — it asks for the user's password: ./setup/install.sh
   Use --yes only if the user explicitly authorized a non-interactive install.
   Do not edit the sources and do not bypass installer checks. On failure, read
   the make.log it points to (e.g. /var/lib/dkms/linuwu_sense/1.0/<kernel>/<arch>/log/make.log)
   and report the exact error instead of guessing.
5. Verify and report the evidence:
   - dkms status shows linuwu_sense/1.0 installed for the running kernel;
   - lsmod | grep linuwu_sense shows the module loaded;
   - command -v nitroctl nitroctl-gui nitroctl-curve nitroctl-restore;
   - nitroctl-curve --status and, on systemd, systemctl status linuwu_sense.service.
   If the module is not loaded, reboot and check again.
6. Tell the user how to use it (nitroctl, nitroctl-gui), that saved settings are
   reapplied at login by nitroctl-restore, and how to undo everything:
   ./setup/install.sh --uninstall

Never skip step 3, and never run destructive or system-wide commands beyond
what the installer does.
```

## Fan curve

The firmware only offers "automatic" or a fixed speed, so nitroctl applies a curve from userspace: **7 levels per fan** (CPU/GPU), each with editable temperature and speed, interpolated linearly. Edit in the GUI (Fan curve card) or CLI (menu item `7`) — changes apply immediately.

Safety rules: never below 20%; suspended GPU holds that fan at 40% idle; failed sensor ramps both fans to 75%; writes only on change, at most every 5 s, with 2 °C hysteresis.

The curve keeps running with the GUI closed via `nitroctl-curve`, started with your session through XDG autostart (works on GNOME, KDE, Xfce, Sway — no systemd). Only one writer at a time; turning the curve off hands the fans back to the firmware.

```bash
nitroctl-curve --status        # show levels and last report
nitroctl-curve --once          # apply a single tick (diagnostics)
nitroctl-curve --restore-auto  # hand both fans back to the firmware
```

## Configuration

`S` in the CLI or *Save configuration* in the GUI stores the current values — thermal profile, fan speeds, battery limiter, RGB timeout, USB charging — in `~/.config/nitroctl/`. The firmware resets those on every boot, so `nitroctl-restore` (XDG autostart) writes them back at login. With the fan curve on, the saved `fan_speed` is skipped: the daemon stays in charge of the fans.

`L` in the CLI or *Load configuration* in the GUI applies the same set on demand, and `nitroctl-restore --verbose` shows exactly what was restored.

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
* [✅] Config save/load, reapplied at login, install guide

## Credits, origin, and AI

Based on the original [nitroctl repository by cani442k](https://github.com/cani442k/nitroctl) — thanks to its author and contributors for the foundation. This fork follows a different idea and direction, with its own goals, features, and design decisions.

AI tools are used in this project's development, and contributors are encouraged to use them to propose improvements and new features. Nothing is accepted automatically: AI-generated or AI-assisted changes land only after human review, testing, and validation for correctness, safety, maintainability, and compatibility.
