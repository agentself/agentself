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
curl -fsSL -o /tmp/age.tgz \
  "https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-linux-${goarch}.tar.gz"
tar -xzf /tmp/age.tgz -C /tmp
install -m 0755 /tmp/age/age "$dest/age"
install -m 0755 /tmp/age/age-keygen "$dest/age-keygen"
curl -fsSL -o "$dest/sops" \
  "https://github.com/getsops/sops/releases/download/${SOPS_VERSION}/sops-${SOPS_VERSION}.linux.${goarch}"
chmod 0755 "$dest/sops"
echo "$dest" >> "$GITHUB_PATH"
sudo apt-get update
sudo apt-get install -y --no-install-recommends pass gnupg
"$dest/age" --version
"$dest/sops" --version
gpg --version
pass version
