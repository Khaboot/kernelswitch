# KernelSwitch

KernelSwitch is an interactive command-line tool for choosing which installed
Linux kernel GRUB should boot by default. It does not install or remove kernels,
and it does not change the currently running kernel. A reboot is needed to
start using a different kernel.

The interface uses optional terminal colors, respects `NO_COLOR`, and does not
need extra Python packages.

## Compatibility

- Linux distributions using GRUB 2
- Python 3.9 or newer
- `grubby` where available (commonly used by Fedora and RHEL-family systems)
- Otherwise, a readable GRUB configuration in `/boot/grub/grub.cfg` or
  `/boot/grub2/grub.cfg`, plus `grub-set-default` or `grub2-set-default`
- `sudo` to change the default or reboot when not running as root

Without `grubby`, KernelSwitch reads GRUB menu entries and uses their IDs to
select a default. This requires GRUB's saved-default mode to be enabled:
`GRUB_DEFAULT=saved` in `/etc/default/grub`. KernelSwitch never changes GRUB
configuration files or regenerates the boot menu for you. Follow your
distribution's documentation to enable saved defaults and regenerate the GRUB
configuration. Custom GRUB layouts and systemd-boot are not currently
supported.

## Install

Install with `pipx`:

```bash
pipx install .
```

Or run it directly:

```bash
python3 kernelswitch.py
```

## Use

```bash
kernelswitch
```

KernelSwitch lists installed GRUB entries and marks the running kernel. Choose
an entry and confirm if it should become the default for future boots. It only
offers to reboot after successfully changing the default, and only if the
selected kernel is not already running. Declining a reboot leaves the selected
kernel as the default for the next boot.

## Tests

Run the test suite with Python's standard library:

```bash
python3 -m unittest -v
```

## License

This project is distributed under the MIT License. See [LICENSE](LICENSE).
