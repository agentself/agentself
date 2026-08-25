#!/usr/bin/env bash
set -euo pipefail
dest="${RUNNER_TEMP}/host-tools"
mkdir -p "$dest"
arch=$(uname -m)
case "$arch" in
  x86_64|amd64) goarch=amd64 ;;
  arm64|aarch64) goarch=arm64 ;;
  *) echo "unsupported arch $arch" >&2; exit 1 ;;
esac
case "${AGE_VERSION}:${goarch}" in
  v1.3.1:amd64) age_sha256=2b233301ad21ab7b1eabd9ae1198a164005fa4928fcdd745d47c39f8593209d7 ;;
  v1.3.1:arm64) age_sha256=01120ea2cbf0463d4c6bd767f99f3271bbed1cdc8a9aa718a76ba1fe4f01998b ;;
  *) echo "no pinned age checksum for ${AGE_VERSION}/${goarch}" >&2; exit 1 ;;
esac
case "${SOPS_VERSION}:${goarch}" in
  v3.13.3:amd64) sops_sha256=42162d5cef10b74fcf80a045a70e658d7ce6e63d6ea1be6f347e44015714468d ;;
  v3.13.3:arm64) sops_sha256=b97c0d434aab577dc40310e8d22ff9e45eef4c80638ab978daae9b4681c59286 ;;
  *) echo "no pinned sops checksum for ${SOPS_VERSION}/${goarch}" >&2; exit 1 ;;
esac
age_archive="${RUNNER_TEMP}/age.tgz"
trap 'rm -rf "$RUNNER_TEMP/age" "$age_archive"' EXIT
curl -fsSL -o "$age_archive" \
  "https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-darwin-${goarch}.tar.gz"
printf '%s  %s\n' "$age_sha256" "$age_archive" | shasum -a 256 -c
tar -xzf "$age_archive" -C "${RUNNER_TEMP}"
install -m 0755 "${RUNNER_TEMP}/age/age" "$dest/age"
install -m 0755 "${RUNNER_TEMP}/age/age-keygen" "$dest/age-keygen"
rm -rf "${RUNNER_TEMP}/age"
rm -f "$age_archive"
trap - EXIT
curl -fsSL -o "$dest/sops" \
  "https://github.com/getsops/sops/releases/download/${SOPS_VERSION}/sops-${SOPS_VERSION}.darwin.${goarch}"
printf '%s  %s\n' "$sops_sha256" "$dest/sops" | shasum -a 256 -c
chmod 0755 "$dest/sops"
echo "$dest" >> "$GITHUB_PATH"
"$dest/age" --version
"$dest/sops" --version
