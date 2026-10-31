import subprocess
import unittest
from unittest.mock import Mock, patch

import kernelswitch


class ParseGrubbyEntriesTests(unittest.TestCase):
    def test_parses_entries_and_ignores_non_kernel_paths(self) -> None:
        output = """index=0
kernel="/boot/vmlinuz-6.12.5-200.fc41.x86_64"
title="Fedora Linux (6.12.5-200.fc41.x86_64)"
initrd="/boot/initramfs-6.12.5-200.fc41.x86_64.img"
index=1
kernel="/boot/vmlinuz-6.11.11-300.fc41.x86_64"
title="Fedora Linux (6.11.11-300.fc41.x86_64)"
index=2
kernel="/boot/other-entry"
title="Other entry"
"""
        self.assertEqual(
            kernelswitch.parse_grubby_entries(output),
            [
                kernelswitch.Kernel(
                    "/boot/vmlinuz-6.12.5-200.fc41.x86_64",
                    "Fedora Linux (6.12.5-200.fc41.x86_64)",
                ),
                kernelswitch.Kernel(
                    "/boot/vmlinuz-6.11.11-300.fc41.x86_64",
                    "Fedora Linux (6.11.11-300.fc41.x86_64)",
                ),
            ],
        )

    def test_deduplicates_kernel_paths(self) -> None:
        output = """index=0
kernel="/boot/vmlinuz-6.12.5.fc41.x86_64"
index=1
kernel="/boot/vmlinuz-6.12.5.fc41.x86_64"
"""
        self.assertEqual(
            kernelswitch.parse_grubby_entries(output),
            [kernelswitch.Kernel("/boot/vmlinuz-6.12.5.fc41.x86_64", "vmlinuz-6.12.5.fc41.x86_64")],
        )


class ParseGrubConfigTests(unittest.TestCase):
    def test_parses_nested_entries_and_grub_menu_ids(self) -> None:
        output = """submenu 'Advanced options for Ubuntu' $menuentry_id_option 'gnulinux-advanced-root' {
    menuentry 'Ubuntu, with Linux 6.8.0-41-generic' $menuentry_id_option 'gnulinux-6.8.0-41-generic-advanced-root' {
        linux /vmlinuz-6.8.0-41-generic root=/dev/sda1
        initrd /initrd.img-6.8.0-41-generic
    }
}
menuentry 'Rescue tools' --id='rescue' {
    linux /boot/vmlinuz-6.7.0-rescue root=/dev/sda1
}
"""

        self.assertEqual(
            kernelswitch.parse_grub_config(output),
            [
                kernelswitch.Kernel(
                    "/vmlinuz-6.8.0-41-generic",
                    "Ubuntu, with Linux 6.8.0-41-generic",
                    "gnulinux-advanced-root>gnulinux-6.8.0-41-generic-advanced-root",
                ),
                kernelswitch.Kernel("/boot/vmlinuz-6.7.0-rescue", "Rescue tools", "rescue"),
            ],
        )

    def test_ignores_linux_lines_outside_menu_entries(self) -> None:
        self.assertEqual(
            kernelswitch.parse_grub_config("linux /vmlinuz-6.8.0\n"),
            [],
        )


class CommandTests(unittest.TestCase):
    @patch.object(kernelswitch, "_find_executable", return_value="/usr/bin/grubby")
    @patch.object(kernelswitch.os, "geteuid", return_value=1000)
    @patch.object(kernelswitch, "_run")
    def test_retries_empty_grubby_output_with_privileges(
        self, run: Mock, geteuid: Mock, find: Mock
    ) -> None:
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "", "permission denied"),
            subprocess.CompletedProcess(
                [],
                0,
                'index=0\nkernel="/boot/vmlinuz-7.2.13-1.surface.fc44.x86_64"\n',
                "",
            ),
        ]

        self.assertEqual(
            kernelswitch.get_kernels(),
            [kernelswitch.Kernel("/boot/vmlinuz-7.2.13-1.surface.fc44.x86_64",
                                 "vmlinuz-7.2.13-1.surface.fc44.x86_64")],
        )
        self.assertEqual(
            run.call_args_list,
            [
                unittest.mock.call(["grubby", "--info=ALL"]),
                unittest.mock.call(["grubby", "--info=ALL"], privileged=True),
            ],
        )
        geteuid.assert_called_once_with()
        find.assert_called_once_with("grubby")

    @patch.object(kernelswitch, "_find_executable", return_value="/usr/bin/grubby")
    @patch.object(kernelswitch.os, "geteuid", return_value=1000)
    @patch.object(kernelswitch, "_run")
    def test_retries_grubby_command_failure_with_privileges(
        self, run: Mock, _geteuid: Mock, _find: Mock
    ) -> None:
        run.side_effect = [
            kernelswitch.KernelSwitchError("permission denied"),
            subprocess.CompletedProcess(
                [],
                0,
                'index=0\nkernel="/boot/vmlinuz-6.8.0"\n',
                "",
            ),
        ]

        self.assertEqual(
            kernelswitch.get_kernels(),
            [kernelswitch.Kernel("/boot/vmlinuz-6.8.0", "vmlinuz-6.8.0")],
        )
        self.assertEqual(
            run.call_args_list,
            [
                unittest.mock.call(["grubby", "--info=ALL"]),
                unittest.mock.call(["grubby", "--info=ALL"], privileged=True),
            ],
        )
        _geteuid.assert_called_once_with()
        _find.assert_called_once_with("grubby")

    @patch.object(kernelswitch, "_find_executable", return_value=None)
    @patch.object(kernelswitch, "_get_grub_config_kernels")
    def test_falls_back_to_grub_config_when_grubby_is_unavailable(
        self, get_grub_config: Mock, _find: Mock
    ) -> None:
        expected = [kernelswitch.Kernel("/vmlinuz-6.8.0", "Linux 6.8.0", "linux-6.8.0")]
        get_grub_config.return_value = expected

        self.assertEqual(kernelswitch.get_kernels(), expected)
        get_grub_config.assert_called_once_with()
        _find.assert_called_once_with("grubby")

    @patch.object(kernelswitch, "_find_executable", return_value="/usr/bin/grubby")
    @patch.object(kernelswitch, "_run")
    def test_reads_default_kernel_with_privileges(self, run: Mock, find: Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "/boot/vmlinuz-test\n", "")

        self.assertEqual(kernelswitch._default_kernel(), "/boot/vmlinuz-test")
        run.assert_called_once_with(["grubby", "--default-kernel"], privileged=True)
        find.assert_called_once_with("grubby")

    @patch.object(kernelswitch, "_grub_default_is_saved", return_value=True)
    @patch.object(kernelswitch, "_find_executable", side_effect=[None, "/usr/bin/grub2-set-default"])
    @patch.object(kernelswitch, "_run")
    def test_sets_default_by_grub_menu_selector(
        self, run: Mock, find: Mock, saved: Mock
    ) -> None:
        kernel = kernelswitch.Kernel(
            "/vmlinuz-6.8.0",
            "Ubuntu, with Linux 6.8.0",
            "gnulinux-6.8.0",
        )

        self.assertEqual(kernelswitch._set_default_kernel(kernel), "gnulinux-6.8.0")
        run.assert_called_once_with(
            ["/usr/bin/grub2-set-default", "gnulinux-6.8.0"],
            privileged=True,
        )
        self.assertEqual(
            find.call_args_list,
            [unittest.mock.call("grubby"), unittest.mock.call("grub-set-default", "grub2-set-default")],
        )
        saved.assert_called_once_with()

    @patch.object(kernelswitch, "_grub_default_is_saved", return_value=False)
    @patch.object(kernelswitch, "_find_executable", return_value=None)
    @patch.object(kernelswitch, "_run")
    def test_refuses_to_change_default_when_saved_mode_is_disabled(
        self, run: Mock, find: Mock, _saved: Mock
    ) -> None:
        kernel = kernelswitch.Kernel("/vmlinuz-6.8.0", "Linux 6.8.0", "linux-6.8.0")

        with self.assertRaisesRegex(kernelswitch.KernelSwitchError, "GRUB_DEFAULT=saved"):
            kernelswitch._set_default_kernel(kernel)
        run.assert_not_called()
        find.assert_called_once_with("grubby")
        _saved.assert_called_once_with()

    @patch.object(kernelswitch.os, "geteuid", return_value=1000)
    @patch.object(kernelswitch.shutil, "which", return_value="/usr/bin/sudo")
    @patch.object(kernelswitch.subprocess, "run")
    def test_privileged_command_uses_sudo_without_a_shell(
        self, run: Mock, which: Mock, geteuid: Mock
    ) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        kernelswitch._run(["grubby", "--set-default", "/boot/vmlinuz-test"], privileged=True)
        run.assert_called_once_with(
            ["/usr/bin/sudo", "--", "grubby", "--set-default", "/boot/vmlinuz-test"],
            capture_output=True,
            text=True,
            check=False,
        )
        which.assert_called_once_with("sudo")
        geteuid.assert_called_once_with()

    @patch.object(kernelswitch, "get_kernels")
    @patch.object(kernelswitch, "_default_kernel", side_effect=["/boot/vmlinuz-current", "/boot/vmlinuz-selected"])
    @patch.object(kernelswitch, "_set_default_kernel", return_value="/boot/vmlinuz-selected")
    @patch.object(kernelswitch.platform, "release", return_value="current")
    @patch.object(kernelswitch, "_run")
    @patch("builtins.input", side_effect=["2", "y", "n"])
    def test_selects_default_without_reboot_when_user_declines_reboot(
        self,
        user_input: Mock,
        run: Mock,
        release: Mock,
        _set_default: Mock,
        default_kernel: Mock,
        get_kernels: Mock,
    ) -> None:
        get_kernels.return_value = [
            kernelswitch.Kernel("/boot/vmlinuz-current", "Current kernel"),
            kernelswitch.Kernel("/boot/vmlinuz-selected", "Selected kernel"),
        ]

        self.assertEqual(kernelswitch.main(), 0)
        _set_default.assert_called_once_with(kernelswitch.Kernel("/boot/vmlinuz-selected", "Selected kernel"))
        self.assertEqual(default_kernel.call_count, 2)
        self.assertEqual(user_input.call_count, 3)
        run.assert_not_called()
        release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
