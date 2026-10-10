#!/bin/sh
# Secret/ConfigMap volumes update atomically; do not mount them using subPath.
set -eu
fingerprint() {
    sha256sum /etc/edge/nginx.conf /etc/nginx/tls/tls.crt \
        /etc/nginx/tls/tls.key /etc/nginx/client-ca/ca.crt
}
nginx -c /etc/edge/nginx.conf -t
last=$(fingerprint)
nginx -c /etc/edge/nginx.conf -g 'daemon off;' &
master=$!
stop() {
    kill -QUIT "$master" 2>/dev/null || true
    wait "$master" || true
    exit 0
}
trap stop TERM INT
while kill -0 "$master" 2>/dev/null; do
    sleep 15 &
    wait $! || true
    current=$(fingerprint) || continue
    if [ "$current" != "$last" ]; then
        if nginx -c /etc/edge/nginx.conf -t && nginx -c /etc/edge/nginx.conf -s reload; then
            last=$current
        fi
    fi
done
wait "$master"
