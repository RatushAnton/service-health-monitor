FROM python:3.13-slim-trixie

LABEL org.opencontainers.image.title="Service Health Monitor"
LABEL org.opencontainers.image.description="Self-hosted network and service monitoring"
LABEL org.opencontainers.image.source="https://github.com/RatushAnton/service-health-monitor"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Create a dedicated, unprivileged container user.
RUN groupadd --system --gid 10001 healthmon \
    && useradd --system --uid 10001 --gid 10001 \
       --home-dir /var/lib/service-health-monitor \
       --shell /usr/sbin/nologin healthmon \
    && mkdir -p /var/lib/service-health-monitor /etc/healthmon \
    && chown -R healthmon:healthmon /var/lib/service-health-monitor

# Install the existing Python application.
COPY pyproject.toml ./
COPY src/ ./src/

RUN python -m pip install .

# Never run the application as root.
USER 10001:10001

ENTRYPOINT ["healthmon"]
CMD ["--help"]
