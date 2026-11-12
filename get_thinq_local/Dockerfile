# A one-shot installer: it copies the integration into /config/custom_components
# and exits. Nothing stays running.
ARG BUILD_FROM
FROM alpine:3.20
RUN apk add --no-cache curl ca-certificates
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/run.sh"]
