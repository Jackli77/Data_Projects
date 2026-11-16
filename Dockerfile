FROM python:3.12-slim

WORKDIR /app

# Install the package (and its dependencies from setup.py), which provides the `kbo` command.
COPY setup.py ./
COPY kbo ./kbo
RUN pip install --no-cache-dir .

# Archives are not baked into the image: mount a host folder here to keep the download cache.
VOLUME /app/data

ENTRYPOINT ["kbo"]
