# Contributing to CompatD

Thanks for helping. CompatD is a pre-release, and the most useful thing right now is testing it on real hardware.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to help

* **Test on your machine.** Run CompatD on Debian, MX Linux, LMDE or Kali, then use **☰ → Send test report…** (or `compatd report --open`). It opens a pre-filled **Hardware test report** issue for you to review and submit. Debian and MX haven't been tested on real hardware yet.
* **Report bugs.** Open a **Bug report** issue. Wrong detections, bad recommendations and false positives from the scanner all count.
* **Suggest features.** Open a **Feature request** issue. Explain the problem it solves first.
* **Send code.** Fix a bug, add a detection rule, or improve a page. See below.

For a security problem, follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## What to include in a report

Run these and paste the output. Look over it first and remove anything you don't want to share.

```sh
compatd info          # distribution, kernel, CPU, session
compatd drivers       # devices and recommendations
compatd system        # device compatibility check
```

For a scanner problem, include the file type and the finding, for example `compatd scan ~/Downloads/app --json`. Don't attach files you're not allowed to share.

If the GUI is involved, say which page and add a screenshot.

## Setting up

Requirements: Python 3.9 or newer, `python3-gi`, `gir1.2-gtk-3.0`, `pciutils`, `usbutils`, `kmod`, `pkexec`. `gcc` is optional; some scanner tests are skipped without it.

```sh
git clone https://github.com/unforged1/CompatD.git
cd CompatD
./bin/compatd          # runs from the source tree, no install needed
make test              # unit tests
```

Detection and scanning are read-only. To try actions without changing your system, turn on **Dry-run mode** under Options. It simulates apt installs and only prints other commands.

## Making changes

1. Fork the repository and create a branch from `main`.
2. Keep each pull request to one change. A bug fix and a new feature go in separate pull requests.
3. Add or update tests in `tests/` for logic changes. Detection rules, the scanner and package checks are all testable without root.
4. Run `make test` and check that everything passes.
5. For GUI changes, check the result in at least one dark and one light theme. Add before and after screenshots to the pull request.
6. Update `CHANGELOG.md` under the unreleased version, and `README.md` if user-visible behaviour changes.

### Code style

* Python 3.9 compatible. Use type hints.
* Follow the style of the surrounding code: 4-space indents, double quotes, lines up to about 150 characters.
* New source files start with the license header:

  ```python
  # SPDX-License-Identifier: GPL-3.0-or-later
  # Copyright (C) 2026 unforged1
  ```

### Safety rules

CompatD runs commands as root, so these rules are strict:

* Pass commands as argv lists. Never build a shell string from file names or package names.
* Every privileged action goes through the confirmation dialog, which shows the exact commands, and then through `pkexec`.
* Detection and scanning never run the files they inspect and never need root.
* A finding's automatic fix must be safe to run as written. If it isn't, describe the fix in the recommendation text and leave the action out.

### Commit messages

Write a short summary line in the imperative ("Fix Wi-Fi firmware lookup for MT7921"), then a blank line and the details if needed.

## License

CompatD is licensed under the GNU General Public License, version 3 or later. By sending a contribution, you agree that it is licensed under the same terms.
