#!/usr/bin/env python3
"""Interactively select the default GRUB kernel on Linux."""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from glob import glob


class KernelSwitchError(Exception):
    """An expected error while inspecting or changing boot configuration."""


@dataclass(frozen=True)
class Kernel:
    path: str
    title: str
    selector: str | None = None

    @property
    def version(self) -> str:
        filename = os.path.basename(self.path)
        if filename.startswith("vmlinuz-"):
            return filename.removeprefix("vmlinuz-")
        version = re.search(r"\b(\d+\.\d+\.\d+[A-Za-z0-9.+_-]*)\b", self.title)
        return version.group(1) if version else filename


def _value(text: str) -> str:
    try:
        values = shlex.split(text)
    except ValueError as error:
        raise KernelSwitchError(f"Could not parse grubby output: {error}") from error
    return values[0] if values else ""


def parse_grubby_entries(output: str) -> list[Kernel]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in output.splitlines():
        key, separator, raw_value = line.strip().partition("=")
        if not separator:
            continue
        if key == "index" or (key == "kernel" and "kernel" in current):
            if current:
                records.append(current)
            current = {}
        current[key] = _value(raw_value)
    if current:
        records.append(current)

    kernels: list[Kernel] = []
    seen_paths: set[str] = set()
    for record in records:
        path = record.get("kernel", "")
        if not path.startswith("/") or not os.path.basename(path).startswith("vmlinuz-"):
            continue
        if path in seen_paths:
            continue
        seen_paths.add(path)
        kernels.append(Kernel(path=path, title=record.get("title") or os.path.basename(path)))
    return kernels


def parse_grub_config(output: str) -> list[Kernel]:
    entries: list[tuple[str, str, str, int]] = []
    kernels: list[Kernel] = []
    seen_selectors: set[str] = set()
    depth = 0

    for line in output.splitlines():
        start = re.match(r"""^\s*(menuentry|submenu)\s+(['"])(.*?)\2(.*?)\{""", line)
        if start:
            kind, title, options = start.group(1), start.group(3), start.group(4)
            identifier = re.search(
                r"""(?:--id(?:=|\s+)|\$menuentry_id_option\s+)(?:['"]([^'"]+)['"]|([^\s{}]+))""",
                options,
            )
            selector_part = (identifier.group(1) or identifier.group(2)) if identifier else title
            entries.append((kind, title, selector_part, depth + 1))

        linux = re.match(r"^\s*linux(?:efi|16)?\s+(\S+)", line)
        if linux and entries:
            path = linux.group(1)
            selector = ">".join(entry[2] for entry in entries)
            if selector not in seen_selectors:
                seen_selectors.add(selector)
                kernels.append(Kernel(path, entries[-1][1], selector))

        quoted = False
        quote_char = ""
        escaped = False
        opens = closes = 0
        for character in line:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif quoted:
                if character == quote_char:
                    quoted = False
            elif character in ("'", '"'):
                quoted = True
                quote_char = character
            elif character == "#":
                break
            elif character == "{":
                opens += 1
            elif character == "}":
                closes += 1
        depth += opens - closes
        while entries and entries[-1][3] > depth:
            entries.pop()

    return kernels


def _find_executable(*names: str) -> str | None:
    return next((path for name in names if (path := shutil.which(name))), None)


def _grub_config_paths() -> list[str]:
    paths = [
        "/boot/grub/grub.cfg",
        "/boot/grub2/grub.cfg",
        *glob("/boot/efi/EFI/*/grub.cfg"),
        *glob("/efi/EFI/*/grub.cfg"),
    ]
    return list(dict.fromkeys(path for path in paths if os.path.isfile(path)))


def _get_grub_config_kernels() -> list[Kernel]:
    for path in _grub_config_paths():
        try:
            with open(path, encoding="utf-8", errors="replace") as config:
                kernels = parse_grub_config(config.read())
        except OSError as error:
            raise KernelSwitchError(f"Could not read GRUB configuration {path}: {error}") from error
        if kernels:
            return kernels
    raise KernelSwitchError(
        "No GRUB kernel entries were found. Check that GRUB is installed and its "
        "configuration is in /boot/grub/grub.cfg or /boot/grub2/grub.cfg."
    )


def _grub_default_is_saved() -> bool:
    try:
        with open("/etc/default/grub", encoding="utf-8") as config:
            lines = config.readlines()
    except OSError as error:
        raise KernelSwitchError(f"Could not read /etc/default/grub: {error}") from error

    for line in lines:
        match = re.match(r"""^\s*GRUB_DEFAULT\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))""", line)
        if match:
            return next(value for value in match.groups() if value is not None) == "saved"
    return False


def _run(arguments: list[str], *, privileged: bool = False) -> subprocess.CompletedProcess[str]:
    command = arguments
    if privileged and os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if sudo is None:
            raise KernelSwitchError("Administrator access is required, but sudo was not found.")
        command = [sudo, "--", *arguments]

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise KernelSwitchError(f"Could not run {arguments[0]}: {error}") from error
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "no error details provided"
        raise KernelSwitchError(f"{arguments[0]} failed: {details}")
    return result


def get_kernels() -> list[Kernel]:
    grubby = _find_executable("grubby")
    if grubby:
        try:
            result = _run(["grubby", "--info=ALL"])
        except KernelSwitchError:
            if os.geteuid() == 0:
                raise
            result = _run(["grubby", "--info=ALL"], privileged=True)
        else:
            if not parse_grubby_entries(result.stdout) and os.geteuid() != 0:
                result = _run(["grubby", "--info=ALL"], privileged=True)
        kernels = parse_grubby_entries(result.stdout)
        if kernels:
            return kernels
    return _get_grub_config_kernels()


def _is_running(kernel: Kernel, running_version: str) -> bool:
    return kernel.version == running_version


def _ask_yes_no(question: str) -> bool:
    while True:
        answer = input(f"{question} [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("", "n", "no"):
            return False
        print("Please answer y or n.")


def _color(text: str, code: str) -> str:
    if not sys.stdout.isatty() or "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return text
    return f"\033[{code}m{text}\033[0m"


def _choose_kernel(kernels: list[Kernel], running_version: str) -> Kernel | None:
    print(f"\n{_color('Installed kernels', '1;36')}")
    for index, kernel in enumerate(kernels, start=1):
        version = _color(kernel.version, "1")
        running = f"  {_color('RUNNING', '1;32')}" if _is_running(kernel, running_version) else ""
        print(f"  {index:>2}. {kernel.title}  [{version}]{running}")

    while True:
        answer = input("\nChoose a kernel number, or q to quit: ").strip().lower()
        if answer in ("q", "quit"):
            return None
        try:
            index = int(answer)
        except ValueError:
            print("Enter a listed number or q.")
            continue
        if 1 <= index <= len(kernels):
            return kernels[index - 1]
        print(f"Choose a number from 1 to {len(kernels)}.")


def _default_kernel(kernel: Kernel | None = None) -> str:
    grubby = _find_executable("grubby")
    if grubby and (kernel is None or kernel.selector is None):
        return _run(["grubby", "--default-kernel"], privileged=True).stdout.strip()

    editenv = _find_executable("grub-editenv", "grub2-editenv")
    if not editenv:
        raise KernelSwitchError("Could not find grubby or grub-editenv to read the default kernel.")
    output = _run([editenv, "list"], privileged=True).stdout
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key == "saved_entry":
            return value.strip()
    raise KernelSwitchError(
        "GRUB has no saved default entry. Configure GRUB_DEFAULT=saved before changing the default."
    )


def _set_default_kernel(kernel: Kernel) -> str:
    grubby = _find_executable("grubby")
    if grubby and kernel.selector is None:
        _run(["grubby", "--set-default", kernel.path], privileged=True)
        return kernel.path

    if not kernel.selector:
        raise KernelSwitchError("The selected GRUB entry has no usable menu selector.")
    if not _grub_default_is_saved():
        raise KernelSwitchError(
            "GRUB is not configured to use its saved default. Set GRUB_DEFAULT=saved in "
            "/etc/default/grub and regenerate the GRUB configuration first."
        )
    set_default = _find_executable("grub-set-default", "grub2-set-default")
    if not set_default:
        raise KernelSwitchError("Could not find grub-set-default or grub2-set-default.")
    _run([set_default, kernel.selector], privileged=True)
    return kernel.selector


def main() -> int:
    try:
        kernels = get_kernels()
    except KernelSwitchError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if not kernels:
        print(
            "No bootable kernel entries were found. Check the GRUB configuration "
            "or install the distribution's kernel-entry management tools."
        )
        return 1

    running_version = platform.release()
    try:
        default_path = _default_kernel(kernels[0])
        print(f"Current default: {default_path}")
    except KernelSwitchError as error:
        print(f"Warning: could not read the current default kernel: {error}", file=sys.stderr)
    print(f"Currently running: {running_version}")

    try:
        selected = _choose_kernel(kernels, running_version)
        if selected is None:
            print("No changes made.")
            return 0

        make_default = _ask_yes_no(f"Set {selected.title} as the default for future boots?")
        if not make_default:
            if not _is_running(selected, running_version):
                print("No changes made. A reboot will not use the selected kernel unless it is set as default.")
            else:
                print("No changes made.")
            return 0

        print("Requesting administrator permission if required...")
        expected_default = _set_default_kernel(selected)
        updated_default = _default_kernel(selected)
        if updated_default != expected_default:
            raise KernelSwitchError(
                f"The bootloader reported default {updated_default!r}, "
                f"not the selected kernel {expected_default!r}."
            )
        print(f"Default kernel set to: {expected_default}")

        if _is_running(selected, running_version):
            print("That kernel is already running; no reboot is needed.")
            return 0

        if _ask_yes_no("Reboot now to start using this kernel?"):
            print("Rebooting...")
            _run(["systemctl", "reboot"], privileged=True)
        else:
            print("The selected kernel will be used at the next reboot.")
        return 0
    except (KernelSwitchError, EOFError, KeyboardInterrupt) as error:
        if isinstance(error, (EOFError, KeyboardInterrupt)):
            print("\nCancelled.")
            return 130
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
