#!/bin/sh
set -e

# Remove any previously written config so only one file is active
rm -f /etc/nginx/conf.d/default.conf

if [ "${USE_SSL:-false}" = "true" ]; then
    echo "[nginx] USE_SSL=true — using SSL config"
    cp /etc/nginx/templates/ssl.conf /etc/nginx/conf.d/default.conf
else
    echo "[nginx] USE_SSL=false — using HTTP-only config"
    cp /etc/nginx/templates/http.conf /etc/nginx/conf.d/default.conf
fi

# nginx reads the certificate once at startup, so a certificate renewed by the
# certbot container would not be served until something restarted nginx — which
# in practice meant not until the next deploy. Reload periodically so a renewal
# actually takes effect. A reload is graceful: in-flight requests are finished
# on the old workers.
if [ "${USE_SSL:-false}" = "true" ]; then
    (
        while :; do
            sleep 12h
            nginx -t 2>/dev/null && nginx -s reload
        done
    ) &
fi

exec nginx -g 'daemon off;'
