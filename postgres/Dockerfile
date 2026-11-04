FROM postgres:15

# postgresql-15-pgvector ships the `vector` extension (RAG embeddings storage).
# The base postgres image already has the PGDG apt repo configured, so this
# resolves without adding a new source.
RUN apt-get update && apt-get install -y \
    pgbackrest \
    postgresql-15-pgvector \
    && rm -rf /var/lib/apt/lists/*

COPY ./postgresql.conf /etc/postgresql/postgresql.conf

COPY ./pgbackrest-archive.conf /etc/pgbackrest.conf


