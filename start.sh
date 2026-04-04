#!/bin/bash
# Writes .streamlit/secrets.toml from Railway environment variables,
# then starts the Streamlit app on the PORT Railway assigns.
# Required env vars (set in Railway dashboard → Variables):
#   FMP_API_KEY, ANTHROPIC_API_KEY, VOYAGE_API_KEY,
#   AUTH_COOKIE_KEY, AUTH_PEPPER,
#   AUTH_ADMIN_NAME, AUTH_ADMIN_HASHED_PASSWORD

set -e

mkdir -p .streamlit

cat > .streamlit/secrets.toml <<EOF
[fmp]
api_key = "${FMP_API_KEY}"

[anthropic]
api_key = "${ANTHROPIC_API_KEY}"

[voyage]
api_key = "${VOYAGE_API_KEY}"

[auth.credentials.usernames.admin]
name = "${AUTH_ADMIN_NAME:-Admin}"
password = "${AUTH_ADMIN_HASHED_PASSWORD}"

[auth]
cookie_name = "valuekit_auth"
cookie_key  = "${AUTH_COOKIE_KEY}"
cookie_expiry_days = 1
pepper = "${AUTH_PEPPER}"
EOF

echo "[start.sh] secrets.toml written"

exec streamlit run frontend/app.py \
  --server.port "${PORT:-8501}" \
  --server.address "0.0.0.0" \
  --server.headless true \
  --browser.gatherUsageStats false
