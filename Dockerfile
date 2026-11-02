FROM python:3.12-slim-bookworm
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY licenses ./licenses
RUN pip install --no-cache-dir .
COPY config.toml ./
CMD ["appmon", "daemon"]
