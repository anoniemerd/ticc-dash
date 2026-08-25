FROM docker.io/library/alpine:3.24.1

EXPOSE 5000
ENV TZ=Etc/UTC
ENV PYTHONUNBUFFERED=1
WORKDIR /server

RUN apk add --no-cache \
    python3 py3-flask py3-gunicorn chrony tzdata

COPY ticc-dash.py /server/

ENV CHRONY_USE_SUDO=false
ENV CHRONY_SOCKET=/var/run/chronyd.sock

ENTRYPOINT ["gunicorn", "ticc-dash:app"]
CMD ["--bind", "0.0.0.0:5000"]