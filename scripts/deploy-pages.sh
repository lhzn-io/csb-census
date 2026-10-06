#!/bin/bash
# Build the csb-census site and publish it to Cloudflare Pages (project csb-census, public preview).
# Data comes from $CSB_SITE_DATA (default ~/data/csb-site-data, rebuilt by `csb-census layers`);
# provider views stay off. Credentials: ~/.config/csb-census/cloudflare.env with CLOUDFLARE_API_TOKEN
# (Pages edit only) and CLOUDFLARE_ACCOUNT_ID. Never commit that file.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${CSB_SITE_DATA:-$HOME/data/csb-site-data}"
WRANGLER="npx --yes wrangler@4"
set -a; . "$HOME/.config/csb-census/cloudflare.env"; set +a

for f in meta.json layers/manifest.json spilhaus/spilhaus/cells.json; do
  [ -f "$DATA/$f" ] || { echo "incomplete site data: $DATA/$f missing"; exit 1; }
done
cd "$REPO/site"
# Reinstall whenever the lockfile is newer than the installed tree.
[ node_modules/.package-lock.json -nt package-lock.json ] || npm ci --no-audit --no-fund
VITE_SHOW_PROVIDERS=false npm run build >/dev/null
# Snapshot the data into the build (the layers step replaces $DATA wholesale, so copy, then check).
rm -rf dist/data && cp -R "$DATA" dist/data
[ -f dist/data/meta.json ] && [ -f dist/data/layers/manifest.json ] || { echo "data copy incomplete"; exit 1; }
if ! $WRANGLER pages project list 2>/dev/null | grep -q "csb-census"; then
  $WRANGLER pages project create csb-census --production-branch main
fi
gen=$(python3 -c "import json;print(json.load(open('dist/data/meta.json'))['generation'])")
$WRANGLER pages deploy dist --project-name csb-census --branch main \
  --commit-hash "$(git -C "$REPO" rev-parse HEAD)" --commit-message "census generation $gen" --commit-dirty=true
echo "DEPLOYED generation $gen $(date -u +%FT%TZ)"
