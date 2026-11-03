#!/usr/bin/env bash
# Whether the address every email and alert links to reaches this site, from the internet.
#
#     deploy/reach.sh https://mahoraga.rubenlekli.com
#
# `PUBLIC_URL` builds the sign-up, password-reset and alert links, and nothing else ever opens it, so a
# wrong one fails only in a stranger's inbox. Twice on the new server (2026-11-03): first the tunnel's
# internal `http://platform-api:8000`, then the old VPS's hostname, and nobody could verify a sign-up.
# The site's `/health` says the address it puts in its links, so an answer from some other server —
# the old VPS still holding the name — is not taken for this one.
set -uo pipefail

url="${1:?usage: deploy/reach.sh PUBLIC_URL}"
url="${url%/}"
# Retried: a tunnel just started takes a few seconds to take traffic.
tries="${REACH_TRIES:-6}"

for ((attempt = 1; attempt <= tries; attempt++)); do
  body=$(curl -fsS --max-time 10 "$url/health" 2>&1)
  if [[ "$body" == *"\"public_url\":\"$url\""* ]]; then
    echo "  $url reaches this site: the links in emails work"
    exit 0
  fi
  [ "$attempt" -lt "$tries" ] && sleep 5
done

printf '  \033[31m%s does not reach this site\033[0m: %s\n' "$url" "${body:-no answer}"
echo "  Every sign-up, password-reset and alert link points there. Set PUBLIC_URL in"
echo "  certificates/server.env to the address people open the site at, and give that hostname"
echo "  to this server's tunnel (Cloudflare: Networks > Tunnels > Public Hostnames)."
exit 1
