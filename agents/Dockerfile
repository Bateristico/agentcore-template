# Shared image for every agent folder. Build arg AGENT selects the folder (spec §2).
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim

ARG AGENT
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DOCKER_CONTAINER=1 \
    AGENT_DIR=/app/agent \
    AGENT_NAME=${AGENT}

WORKDIR /app
COPY _kit /app/_kit
# requirements.lock pins the kit's third-party deps to uv.lock (regenerate: README §8; G1 checks it is current).
RUN pip install -c /app/_kit/requirements.lock /app/_kit
COPY ${AGENT}/requirements.txt /app/agent/requirements.txt
RUN pip install -r /app/agent/requirements.txt
COPY ${AGENT} /app/agent

RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app
EXPOSE 8080
CMD ["python", "-m", "agentkit.server"]
