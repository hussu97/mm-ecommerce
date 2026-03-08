FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY conf.d/ssl.conf /etc/nginx/conf.d/ssl.conf
COPY conf.d/http.conf /etc/nginx/conf.d/http.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
