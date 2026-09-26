# shellcheck shell=bash
# Battery-backed real-time clock, so the Pi keeps time without a network.

RTC_UDEV_RULE="/etc/udev/rules.d/85-sdpi-rtc.rules"
RTC_BEGIN="# BEGIN sdpi rtc"
RTC_END="# END sdpi rtc"

rtc_label() { echo "RTC module"; }

_rtc_is_pi5() { [[ "$(pi_model)" == *"Raspberry Pi 5"* ]]; }

_rtc_overlay_chip() {
  grep -Eo '^[[:space:]]*dtoverlay=i2c-rtc,[a-z0-9]+' "$(boot_config_file)" 2>/dev/null \
    | tail -n 1 | sed 's/.*i2c-rtc,//' || true
}

rtc_installed() { _rtc_is_pi5 || [[ -n "$(_rtc_overlay_chip)" ]]; }

rtc_detail() {
  if _rtc_is_pi5; then echo "built into the Pi 5"; return 0; fi
  if [[ -e /dev/rtc0 ]]; then
    echo "$(_rtc_overlay_chip), active"
  else
    echo "$(_rtc_overlay_chip), reboot to activate"
  fi
}

rtc_install() {
  if _rtc_is_pi5; then
    log "This is a Raspberry Pi 5, which has a built-in RTC. Connect a battery to its RTC header; there's nothing to set up."
    return 0
  fi

  local cfg chip found chips=()
  cfg="$(boot_config_file)"

  log "Enabling I2C..."
  if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_i2c 0
  else
    grep -q '^dtparam=i2c_arm=on' "$cfg" || echo "dtparam=i2c_arm=on" >> "$cfg"
    modprobe i2c-dev || true
  fi
  # i2c-tools for the bus scan; util-linux-extra provides hwclock on trixie.
  apt_install i2c-tools util-linux-extra

  if [[ -e /dev/i2c-1 ]]; then
    log "Scanning the I2C bus for an RTC..."
    found="$(_rtc_scan)"
  else
    warn "The I2C bus won't exist until after a reboot, so it can't be scanned yet."
    found="noscan"
  fi

  case "$found" in
    68)
      echo "Found a device at address 0x68. The DS3231, DS1307 and PCF8523 all use that address."
      chips=(ds3231 ds1307 pcf8523) ;;
    51)
      echo "Found a device at address 0x51 (PCF8563 or PCF85063)."
      chips=(pcf8563 pcf85063) ;;
    noscan)
      chips=(ds3231 ds1307 pcf8523 pcf8563 pcf85063) ;;
    *)
      warn "No RTC found at 0x68 or 0x51. Check the wiring: SDA to GPIO2 (pin 3), SCL to GPIO3 (pin 5), 3.3V and GND."
      confirm "Pick a chip anyway?" n || return 1
      chips=(ds3231 ds1307 pcf8523 pcf8563 pcf85063) ;;
  esac
  chip="$(choose "Which RTC chip is on your module?" "${chips[@]}")"

  log "Adding dtoverlay=i2c-rtc,$chip to $cfg (backup at $cfg.sdpi-bak)..."
  cp "$cfg" "$cfg.sdpi-bak"
  _rtc_strip_overlay "$cfg"
  [[ -z "$(tail -c 1 "$cfg")" ]] || echo >> "$cfg"
  printf '%s\n[all]\ndtoverlay=i2c-rtc,%s\n%s\n' "$RTC_BEGIN" "$chip" "$RTC_END" >> "$cfg"

  _rtc_write_udev_rule

  log "Removing fake-hwclock (it fakes an RTC by saving the time at shutdown and would fight with a real one)..."
  apt-get remove -y fake-hwclock || true

  touch "$REBOOT_FLAG"
  echo
  log "RTC configured for the $chip. Reboot to activate it."
  echo "Once the Pi is online after that, the kernel copies network time into the RTC"
  echo "every 11 minutes on its own. Update > RTC module writes it immediately instead."
}

# Writes the current (network-synced) time into the RTC right away.
rtc_update() {
  if _rtc_is_pi5; then log "The Pi 5's built-in RTC is kept in sync automatically."; return 0; fi
  if [[ ! -e /dev/rtc0 ]]; then
    warn "The RTC isn't active yet; reboot first."
    return 1
  fi
  if [[ "$(timedatectl show --property=NTPSynchronized --value)" != yes ]]; then
    warn "The system clock hasn't synced with network time yet, so it's not saved to the RTC. Try again once the Pi is online."
    return 1
  fi
  hwclock --rtc=/dev/rtc0 --systohc --utc
  log "Saved $(date) to the RTC."
}

rtc_remove() {
  if _rtc_is_pi5; then log "The Pi 5's RTC is built in; there's nothing to remove."; return 0; fi
  local cfg
  cfg="$(boot_config_file)"
  log "Removing the RTC overlay from $cfg (backup at $cfg.sdpi-bak)..."
  cp "$cfg" "$cfg.sdpi-bak"
  _rtc_strip_overlay "$cfg"
  rm -f "$RTC_UDEV_RULE"
  udevadm control --reload-rules || true
  log "Reinstalling fake-hwclock so the clock survives reboots without an RTC..."
  apt_install fake-hwclock
  touch "$REBOOT_FLAG"
  log "RTC removed. I2C is left enabled. Reboot to finish."
}

# Prints 68 or 51 when a device answers there (UU = a driver already owns it).
_rtc_scan() {
  local out c68 c51
  out="$(i2cdetect -y 1 2>/dev/null)" || return 0
  c68="$(awk '/^60:/{print $10}' <<<"$out")"
  c51="$(awk '/^50:/{print $3}' <<<"$out")"
  if [[ "$c68" == 68 || "$c68" == UU ]]; then
    echo 68
  elif [[ "$c51" == 51 || "$c51" == UU ]]; then
    echo 51
  fi
}

# Removes our marked block, plus any hand-added i2c-rtc overlay (e.g. from
# following the old manual README steps) so there's never more than one.
_rtc_strip_overlay() {
  sed -i -e "/^$RTC_BEGIN\$/,/^$RTC_END\$/d" -e '/^[[:space:]]*dtoverlay=i2c-rtc/d' "$1"
}

# The Pi kernel builds RTC drivers as modules, so its own boot-time RTC read
# happens before the driver exists, and trixie no longer ships a udev hook to
# do it later. This sets the system clock when the RTC appears.
_rtc_write_udev_rule() {
  cat > "$RTC_UDEV_RULE" <<'EOF'
# Installed by sdpi's RTC module: set the system clock from the add-on RTC when its driver loads.
ACTION=="add", SUBSYSTEM=="rtc", KERNEL=="rtc0", RUN+="/usr/sbin/hwclock --rtc=/dev/%k --hctosys --utc"
EOF
  udevadm control --reload-rules || true
}
