#!/usr/bin/env bash
# Build an ESP Flasher UI AppImage.
#
# Usage: build_appimage.sh <light|full>
#   light: PyQt6 + pyserial only
#   full:  PyQt6 + pyserial + esphome + esptool + platformio
#
# Output: dist/esp-flasher-ui-<version>-<variant>-x86_64.AppImage
# Requires on PATH: wget, tar, file, awk. FUSE is not needed.
set -euo pipefail

VARIANT="${1:?missing variant (light|full)}"

if [[ "${VARIANT}" != "light" && "${VARIANT}" != "full" ]]; then
    echo "ERROR: variant must be 'light' or 'full', got '${VARIANT}'" >&2
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${REPO_ROOT}/build/${VARIANT}"
APPDIR="${BUILD_ROOT}/AppDir"
DIST="${REPO_ROOT}/dist"

PBS_RELEASE="${PBS_RELEASE:-20260610}"
PBS_PY_VERSION="${PBS_PY_VERSION:-3.12.13}"
PBS_TRIPLE="x86_64-unknown-linux-gnu"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/cpython-${PBS_PY_VERSION}+${PBS_RELEASE}-${PBS_TRIPLE}-install_only.tar.gz"

VERSION="$(awk -F'"' '/^__version__/ {print $2; exit}' "${REPO_ROOT}/app/__init__.py")"
if [[ -z "${VERSION}" ]]; then
    echo "ERROR: could not parse __version__ from app/__init__.py" >&2
    exit 2
fi
echo "==> Building ESP Flasher UI ${VERSION} (variant=${VARIANT})"
echo "    Embedded Python: ${PBS_PY_VERSION} (release ${PBS_RELEASE})"

rm -rf "${BUILD_ROOT}"
mkdir -p "${BUILD_ROOT}" "${APPDIR}/usr" "${DIST}"

PYDIR="${APPDIR}/usr/lib/python"
mkdir -p "${PYDIR}"

echo "==> Downloading python-build-standalone"
wget --quiet --show-progress --output-document "${BUILD_ROOT}/python.tar.gz" "${PBS_URL}"

echo "==> Extracting Python runtime"
tar -xzf "${BUILD_ROOT}/python.tar.gz" -C "${BUILD_ROOT}"
mv "${BUILD_ROOT}/python/"* "${PYDIR}/"
rmdir "${BUILD_ROOT}/python"

PY="${PYDIR}/bin/python3.12"
"${PY}" --version

echo "==> Upgrading pip + wheel"
"${PY}" -m pip install --no-warn-script-location --upgrade pip wheel

echo "==> Installing ${VARIANT} dependencies"
if [[ "${VARIANT}" == "light" ]]; then
    "${PY}" -m pip install --no-warn-script-location \
        "PyQt6>=6.11.0" "pyserial>=3.5"
else
    "${PY}" -m pip install --no-warn-script-location \
        -r "${REPO_ROOT}/requirements.txt"
fi

echo "==> Copying application source"
APP_SHARE="${APPDIR}/usr/share/esp-flasher-ui"
mkdir -p "${APP_SHARE}"
cp "${REPO_ROOT}/main.py" "${APP_SHARE}/main.py"
cp -r "${REPO_ROOT}/app" "${APP_SHARE}/app"

if [[ "${VARIANT}" == "full" ]]; then
    echo "==> Installing espota.py shim"
    cat > "${PYDIR}/bin/espota.py" <<'ESPOTA'
#!/usr/bin/env python3
"""Legacy ArduinoOTA espota.py CLI, routed through esphome.espota2.run_ota."""
import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="espota.py")
    parser.add_argument("-i", "--ip", required=True)
    parser.add_argument("-p", "--port", type=int, default=3232)
    parser.add_argument("-a", "--auth", default="")
    parser.add_argument("-f", "--file", required=True)
    args, _ = parser.parse_known_args()

    from esphome.espota2 import run_ota
    return int(run_ota(args.ip, args.port, args.auth, args.file, None) or 0)


if __name__ == "__main__":
    sys.exit(main())
ESPOTA
    chmod +x "${PYDIR}/bin/espota.py"
fi

echo "==> Rewriting pip script shebangs for relocatability"
# pip bakes absolute paths into shebangs; rewrite to /usr/bin/env python3.12.
for script in "${PYDIR}/bin/"*; do
    [[ -f "${script}" && ! -L "${script}" ]] || continue
    if head -c2 "${script}" 2>/dev/null | grep -q '^#!'; then
        first_line="$(head -n1 "${script}")"
        case "${first_line}" in
            *python3.12*|*python3*|*python*)
                sed -i '1c\#!/usr/bin/env python3.12' "${script}"
                ;;
        esac
    fi
done

echo "==> Pruning bytecode cache + stdlib test directories"
find "${PYDIR}" -type d -name __pycache__ -prune -exec rm -rf {} +
STDLIB="${PYDIR}/lib/python3.12"
rm -rf "${STDLIB}/test" "${STDLIB}/idlelib/idle_test" 2>/dev/null || true

echo "==> Composing AppDir"
mkdir -p "${APPDIR}/usr/share/applications" "${APPDIR}/usr/share/icons/hicolor/scalable/apps"
cp "${REPO_ROOT}/build-tools/esp-flasher-ui.desktop" "${APPDIR}/esp-flasher-ui.desktop"
cp "${REPO_ROOT}/build-tools/esp-flasher-ui.desktop" "${APPDIR}/usr/share/applications/esp-flasher-ui.desktop"
cp "${REPO_ROOT}/app/assets/esp-flasher-ui.svg" "${APPDIR}/esp-flasher-ui.svg"
cp "${REPO_ROOT}/app/assets/esp-flasher-ui.svg" "${APPDIR}/usr/share/icons/hicolor/scalable/apps/esp-flasher-ui.svg"

cat > "${APPDIR}/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
PY="${HERE}/usr/lib/python/bin/python3.12"
export PATH="${HERE}/usr/lib/python/bin:${PATH}"
exec "${PY}" "${HERE}/usr/share/esp-flasher-ui/main.py" "$@"
APPRUN
chmod +x "${APPDIR}/AppRun"

APPIMAGETOOL_DIR="${BUILD_ROOT}/appimagetool"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
if [[ ! -x "${APPIMAGETOOL_DIR}/AppRun" ]]; then
    echo "==> Fetching appimagetool"
    mkdir -p "${APPIMAGETOOL_DIR}"
    wget --quiet --output-document "${BUILD_ROOT}/appimagetool.AppImage" "${APPIMAGETOOL_URL}"
    chmod +x "${BUILD_ROOT}/appimagetool.AppImage"
    (cd "${APPIMAGETOOL_DIR}" && "${BUILD_ROOT}/appimagetool.AppImage" --appimage-extract >/dev/null)
    mv "${APPIMAGETOOL_DIR}/squashfs-root/"* "${APPIMAGETOOL_DIR}/"
    rmdir "${APPIMAGETOOL_DIR}/squashfs-root" 2>/dev/null || true
fi

OUTPUT="${DIST}/esp-flasher-ui-${VERSION}-${VARIANT}-x86_64.AppImage"
echo "==> Packing -> ${OUTPUT}"
ARCH=x86_64 "${APPIMAGETOOL_DIR}/AppRun" --no-appstream "${APPDIR}" "${OUTPUT}"

size="$(du -h "${OUTPUT}" | cut -f1)"
size_bytes="$(stat -c %s "${OUTPUT}")"
echo "==> Done. Output: ${OUTPUT} (${size}, ${size_bytes} bytes)"

case "${VARIANT}" in
    light) MIN_MB=40 ;;
    full)  MIN_MB=180 ;;
esac
if (( size_bytes < MIN_MB * 1024 * 1024 )); then
    echo "ERROR: ${VARIANT} AppImage is smaller than ${MIN_MB} MB (${size})" >&2
    echo "       Pip packages or the runtime were not bundled correctly." >&2
    exit 1
fi
