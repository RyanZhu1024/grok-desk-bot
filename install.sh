#!/usr/bin/env bash
# Install Desk Assistant or Haro on Raspberry Pi OS and enable a systemd service.
#
#   ./install.sh
#   ./install.sh --with-vosk
#   ./install.sh --haro
#
# Then paste the webhook URL and key into /etc/desk-assistant/config.yaml
# and reboot. On a laptop, use: python -m desk_assistant --test
# Haro on a laptop: python -m desk_assistant --variant haro --test

set -euo pipefail

WITH_VOSK=0
VARIANT=desk
for arg in "$@"; do
  case "${arg}" in
    --with-vosk) WITH_VOSK=1 ;;
    --haro) VARIANT=haro; WITH_VOSK=1 ;;
    -h|--help)
      sed -n '2,10p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: ${arg}" >&2
      exit 1
      ;;
  esac
done

if [[ ! -r /proc/device-tree/model ]] || ! tr -d '\0' < /proc/device-tree/model | grep -q "Raspberry Pi"; then
  echo "install.sh is for Raspberry Pi OS. On a laptop, run: python -m desk_assistant --test" >&2
  echo "For Haro: python -m desk_assistant --variant haro --test" >&2
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo env INSTALL_USER="${SUDO_USER:-$USER}" bash "$0" "$@"
fi

INSTALL_USER="${INSTALL_USER:-${SUDO_USER:-pi}}"
if ! id "${INSTALL_USER}" >/dev/null 2>&1; then
  echo "User ${INSTALL_USER} does not exist. Run as that user, or set INSTALL_USER." >&2
  exit 1
fi

INSTALL_DIR=/opt/desk-assistant
CONFIG_DIR=/etc/desk-assistant
STATE_DIR=/var/lib/desk-assistant
MODEL_DIR="${STATE_DIR}/models"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_HOME="$(getent passwd "${INSTALL_USER}" | cut -d: -f6)"

if [[ "${VARIANT}" == "haro" ]]; then
  echo "Installing Haro for ${INSTALL_USER}"
else
  echo "Installing Desk Assistant for ${INSTALL_USER}"
fi

apt-get update
PACKAGES=(
  python3
  python3-venv
  python3-pip
  python3-gpiozero
  python3-lgpio
  espeak-ng
  alsa-utils
  ca-certificates
  unzip
  curl
  rsync
)
if [[ "${VARIANT}" == "haro" ]]; then
  PACKAGES+=(python3-spidev)
  usermod -aG audio,gpio,spi,i2c "${INSTALL_USER}" || true
else
  PACKAGES+=(
    libsdl2-2.0-0
    libsdl2-image-2.0-0
    libsdl2-mixer-2.0-0
    libsdl2-ttf-2.0-0
  )
  usermod -aG audio,gpio,video,render,input "${INSTALL_USER}" || true
fi
apt-get install -y "${PACKAGES[@]}"

mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}" "${STATE_DIR}" "${MODEL_DIR}"
rsync -a \
  --exclude .git \
  --exclude .venv \
  --exclude state \
  --exclude models \
  --exclude __pycache__ \
  --exclude .pytest_cache \
  --exclude tests \
  "${SCRIPT_DIR}/" "${INSTALL_DIR}/"

python3 -m venv --system-site-packages "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
if [[ "${VARIANT}" == "haro" ]]; then
  PIP_SPEC="${INSTALL_DIR}[pi]"
else
  PIP_SPEC="${INSTALL_DIR}[desk]"
fi
if ! "${INSTALL_DIR}/.venv/bin/pip" install "${PIP_SPEC}"; then
  echo "pip install failed; installing runtime packages directly." >&2
  if [[ "${VARIANT}" != "haro" ]]; then
    apt-get install -y python3-pygame || true
  fi
  "${INSTALL_DIR}/.venv/bin/pip" install "httpx>=0.27,<1" "PyYAML>=6,<7"
  if [[ "${WITH_VOSK}" -eq 1 ]]; then
    "${INSTALL_DIR}/.venv/bin/pip" install "vosk>=0.3.45" || true
  fi
  "${INSTALL_DIR}/.venv/bin/pip" install --no-deps "${INSTALL_DIR}"
fi

if [[ "${WITH_VOSK}" -eq 1 ]]; then
  "${INSTALL_DIR}/.venv/bin/pip" install "vosk>=0.3.45"
  if [[ ! -d "${MODEL_DIR}/vosk-model-small-en-us-0.15" ]]; then
    tmp="$(mktemp -d)"
    curl -L --fail -o "${tmp}/vosk.zip" \
      https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip -q "${tmp}/vosk.zip" -d "${MODEL_DIR}"
    rm -rf "${tmp}"
  fi
fi

if [[ ! -f "${CONFIG_DIR}/config.yaml" ]]; then
  if [[ "${VARIANT}" == "haro" ]]; then
    cp "${INSTALL_DIR}/config.haro.example.yaml" "${CONFIG_DIR}/config.yaml"
  else
    cp "${INSTALL_DIR}/config.example.yaml" "${CONFIG_DIR}/config.yaml"
  fi
  echo "Wrote ${CONFIG_DIR}/config.yaml"
else
  echo "Keeping existing ${CONFIG_DIR}/config.yaml"
fi
if [[ ! -f "${CONFIG_DIR}/desk-assistant.env" ]]; then
  cp "${INSTALL_DIR}/deploy/desk-assistant.env.example" "${CONFIG_DIR}/desk-assistant.env"
fi
chmod 600 "${CONFIG_DIR}/config.yaml" "${CONFIG_DIR}/desk-assistant.env"
chown "${INSTALL_USER}:${INSTALL_USER}" "${CONFIG_DIR}/config.yaml" "${CONFIG_DIR}/desk-assistant.env"
chown -R "${INSTALL_USER}:${INSTALL_USER}" "${STATE_DIR}"

sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
    -e "s|__USER__|${INSTALL_USER}|g" \
    -e "s|__HOME__|${USER_HOME}|g" \
    "${INSTALL_DIR}/deploy/desk-assistant.service" > /etc/systemd/system/desk-assistant.service
sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
    -e "s|__USER__|${INSTALL_USER}|g" \
    -e "s|__HOME__|${USER_HOME}|g" \
    "${INSTALL_DIR}/deploy/haro.service" > /etc/systemd/system/haro.service
chmod 644 /etc/systemd/system/desk-assistant.service /etc/systemd/system/haro.service
chmod 755 "${INSTALL_DIR}/deploy/run.sh"

ln -sfn "${INSTALL_DIR}/.venv/bin/desk-assistant" /usr/local/bin/desk-assistant
ln -sfn "${INSTALL_DIR}/.venv/bin/haro" /usr/local/bin/haro

if [[ "${VARIANT}" == "haro" ]]; then
  BOOT_CONFIG=/boot/firmware/config.txt
  if [[ ! -f "${BOOT_CONFIG}" && -f /boot/config.txt ]]; then
    BOOT_CONFIG=/boot/config.txt
  fi
  if [[ -f "${BOOT_CONFIG}" ]] && ! grep -q '^dtparam=spi=on' "${BOOT_CONFIG}"; then
    printf '\n# Haro APA102 eyes on the ReSpeaker HAT\ndtparam=spi=on\n' >> "${BOOT_CONFIG}"
    echo "Enabled SPI in ${BOOT_CONFIG}"
  fi
fi

systemctl daemon-reload
if [[ "${VARIANT}" == "haro" ]]; then
  systemctl disable --now desk-assistant.service >/dev/null 2>&1 || true
  systemctl enable haro.service
  CHECK_CMD="haro --check"
  LOG_UNIT="haro"
  NEXT="Reboot, then finish the ReSpeaker overlay steps in the README if the mic is not already listed by arecord -l."
else
  systemctl disable --now haro.service >/dev/null 2>&1 || true
  systemctl enable desk-assistant.service
  CHECK_CMD="desk-assistant --check"
  LOG_UNIT="desk-assistant"
  NEXT="Reboot so the audio and GPIO groups and the display session are ready."
fi

cat <<EOF

Installed. The ${LOG_UNIT} service is enabled and will start on boot.

Paste the webhook URL and key before you rely on it:

  sudoedit ${CONFIG_DIR}/config.yaml

    webhook:
      url: "https://your-assistant.example/webhook/..."
      key: "paste-the-bearer-key-here"

Check the result (this also prints the ntfy topic):

  sudo -u ${INSTALL_USER} ${CHECK_CMD}

${NEXT}

  sudo reboot

Logs: journalctl -u ${LOG_UNIT} -f
EOF
