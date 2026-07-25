#!/bin/sh
set -e

POS_HOST=pos.meltingmomentscakes.com
POS_CERT="/etc/letsencrypt/live/${POS_HOST}/fullchain.pem"

# Remove any previously written config so only one file is active
rm -f /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/pos.conf

if [ "${USE_SSL:-false}" = "true" ]; then
    echo "[nginx] USE_SSL=true — using SSL config"
    cp /etc/nginx/templates/ssl.conf /etc/nginx/conf.d/default.conf
else
    echo "[nginx] USE_SSL=false — using HTTP-only config"
    cp /etc/nginx/templates/http.conf /etc/nginx/conf.d/default.conf
fi

# The register's server block names a certificate file, and nginx refuses to
# start when one is missing — so shipping it before the certificate exists
# would take the storefront API down alongside it. Add the block only once the
# file is really there, and re-check periodically so the register comes online
# by itself the first time certbot issues one, without waiting for a deploy.
sync_pos_block() {
    [ "${USE_SSL:-false}" = "true" ] || return 1

    if [ -f "$POS_CERT" ] && [ ! -f /etc/nginx/conf.d/pos.conf ]; then
        echo "[nginx] certificate found for ${POS_HOST} — serving the register API"
        cp /etc/nginx/templates/pos.conf /etc/nginx/conf.d/pos.conf
        return 0
    fi
    if [ ! -f "$POS_CERT" ] && [ -f /etc/nginx/conf.d/pos.conf ]; then
        echo "[nginx] certificate for ${POS_HOST} is gone — withdrawing its block"
        rm -f /etc/nginx/conf.d/pos.conf
        return 0
    fi
    return 1  # nothing changed
}

sync_pos_block || true
if [ "${USE_SSL:-false}" = "true" ] && [ ! -f "$POS_CERT" ]; then
    echo "[nginx] no certificate for ${POS_HOST} yet — the register API is not being served."
    echo "[nginx] point ${POS_HOST} at this host and issue one; nginx will pick it up on its own."
fi

# nginx reads the certificate once at startup, so a certificate renewed by the
# certbot container would not be served until something restarted nginx — which
# in practice meant not until the next deploy. Reload periodically so a renewal
# actually takes effect. A reload is graceful: in-flight requests are finished
# on the old workers.
if [ "${USE_SSL:-false}" = "true" ]; then
    (
        while :; do
            # Poll briskly while the register is still waiting for its
            # certificate so it goes live minutes after issuance, then settle
            # into the renewal cadence once it is being served.
            if [ -f /etc/nginx/conf.d/pos.conf ]; then
                sleep 12h
            else
                sleep 5m
            fi
            sync_pos_block || true
            nginx -t 2>/dev/null && nginx -s reload
        done
    ) &
fi

exec nginx -g 'daemon off;'
