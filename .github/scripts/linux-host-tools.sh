#!/usr/bin/env bash
set -euo pipefail
dest="${RUNNER_TEMP}/host-tools"
mkdir -p "$dest"
arch=$(uname -m)
case "$arch" in
  x86_64|amd64) goarch=amd64 ;;
  aarch64|arm64) goarch=arm64 ;;
  *) echo "unsupported arch $arch" >&2; exit 1 ;;
esac
case "${AGE_VERSION}:${goarch}" in
  v1.3.1:amd64) age_sha256=bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377 ;;
  v1.3.1:arm64) age_sha256=c6878a324421b69e3e20b00ba17c04bc5c6dab0030cfe55bf8f68fa8d9e9093a ;;
  *) echo "no pinned age checksum for ${AGE_VERSION}/${goarch}" >&2; exit 1 ;;
esac
case "${SOPS_VERSION}:${goarch}" in
  v3.13.3:amd64) sops_sha256=e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b ;;
  v3.13.3:arm64) sops_sha256=53b0abacd38ef1b12a66d6c100956691b9cefce018d91f81e73ddf7438b94d77 ;;
  *) echo "no pinned sops checksum for ${SOPS_VERSION}/${goarch}" >&2; exit 1 ;;
esac
age_archive="${RUNNER_TEMP}/age.tgz"
trap 'rm -rf "$RUNNER_TEMP/age" "$age_archive"' EXIT
curl -fsSL -o "$age_archive" \
  "https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-linux-${goarch}.tar.gz"
printf '%s  %s\n' "$age_sha256" "$age_archive" | sha256sum --check --status
tar -xzf "$age_archive" -C "$RUNNER_TEMP"
install -m 0755 "$RUNNER_TEMP/age/age" "$dest/age"
install -m 0755 "$RUNNER_TEMP/age/age-keygen" "$dest/age-keygen"
rm -rf "$RUNNER_TEMP/age"
rm -f "$age_archive"
trap - EXIT
curl -fsSL -o "$dest/sops" \
  "https://github.com/getsops/sops/releases/download/${SOPS_VERSION}/sops-${SOPS_VERSION}.linux.${goarch}"
printf '%s  %s\n' "$sops_sha256" "$dest/sops" | sha256sum --check --status
chmod 0755 "$dest/sops"
echo "$dest" >> "$GITHUB_PATH"
sudo apt-get update
sudo apt-get install -y --no-install-recommends pass gnupg
"$dest/age" --version
"$dest/sops" --version
gpg --version
pass version
