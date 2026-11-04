FROM ghcr.io/home-assistant/base:3.24

RUN apk add --no-cache python3

COPY app /app
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
