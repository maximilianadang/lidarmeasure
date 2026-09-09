#!/bin/bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo "Usage: sudo $0 USER [DEVICE_SERIAL]" >&2
  exit 1
fi
account="${1:-${SUDO_USER:-}}"
serial="${2:-1052684}"
if [[ -z "$account" || ! "$serial" =~ ^[0-9]+$ ]]; then
  echo "Specify a local user and a numeric device serial." >&2
  exit 1
fi
owner_id="$(id -u "$account")"
rule_path="/etc/udev/rules.d/99-lidarmeasure-$serial.rules"
printf -v rule 'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="0e0d", ATTR{idProduct}=="0013", ATTR{serial}=="%s", OWNER="%s", MODE="0600"' "$serial" "$owner_id"
if [[ -e "$rule_path" ]]; then
  if [[ "$(cat "$rule_path")" != "$rule" ]]; then
    echo "Different rule already exists: $rule_path; inspect before replacing." >&2
    exit 1
  fi
else
  printf '%s\n' "$rule" | install -m 0644 /dev/stdin "$rule_path"
fi
udevadm control --reload-rules
udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=0e0d --attr-match=idProduct=0013 --attr-match="serial=$serial"
udevadm settle
echo "Persistent access installed for $account and MultiHarp $serial."
