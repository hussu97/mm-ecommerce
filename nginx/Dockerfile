FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY conf.d/ssl.conf /etc/nginx/templates/ssl.conf
COPY conf.d/http.conf /etc/nginx/templates/http.conf
COPY conf.d/pos.conf /etc/nginx/templates/pos.conf
# Default upstreams (slot A). The compose bind-mount overlays this at runtime
# so cutover can retarget without rebuilding; the COPY is the fallback if the
# volume is missing (a local `docker build` of this image).
COPY runtime/upstreams.conf /etc/nginx/runtime/upstreams.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
