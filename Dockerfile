FROM python:3.12-slim

WORKDIR /app

# Dependencies first, in their own layer: it is rebuilt only when pyproject.toml changes,
# not on every edit of the source code.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# The package itself, which provides the `kbo` command.
COPY README.md ./
COPY kbo ./kbo
RUN pip install --no-cache-dir --no-deps .

# Run as an unprivileged user. Archives are not baked into the image: mount a host
# folder on /app/data to keep the download cache.
RUN useradd --create-home --uid 1000 kbo \
    && mkdir /app/data \
    && chown kbo:kbo /app/data
USER kbo
VOLUME /app/data

ENTRYPOINT ["kbo"]
