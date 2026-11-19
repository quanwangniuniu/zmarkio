FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc

# Install dependencies
RUN apt-get update && apt-get install -y \
    libpq5 \
    libssl3 \
    libxml2 \
    liblz4-1 \
    libzstd1 \
    libbz2-1.0 \
    libyaml-0-2 \
    libssh2-1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install pgbackrest, cron    
RUN apt-get update && apt-get install -y \
    pgbackrest \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Create directory
RUN mkdir -p /etc/pgbackrest /etc/pgbackrest/conf.d /var/log/pgbackrest /var/lib/pgbackrest 

# Copy pre-built file
# COPY ./package/pgbackrest /usr/bin/pgbackrest 
COPY ./pgbackrest.conf /etc/pgbackrest/pgbackrest.conf
COPY ./entrypoint /usr/bin/entrypoint
COPY ./crontab.txt /etc/pgbackrest/crontab.txt

# Assign permission
RUN chmod -R 755 /etc/pgbackrest /var/log/pgbackrest /var/lib/pgbackrest

# Create postgres user
# RUN groupadd -g 999 postgres
# RUN useradd -u 999 -g 999 -m -s /bin/bash postgres

# Change default UID,GID of postgres, keep it same with one in postgres container
RUN usermod -u 999 postgres && groupmod -g 999 postgres

# Add backup crontab
RUN crontab /etc/pgbackrest/crontab.txt

CMD ["tail", "-f", "/dev/null"]
