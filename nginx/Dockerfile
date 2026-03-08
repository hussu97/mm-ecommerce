FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY conf.d/ssl.conf /etc/nginx/templates/ssl.conf
COPY conf.d/http.conf /etc/nginx/templates/http.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
