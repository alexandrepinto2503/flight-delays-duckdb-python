# Flight delay analytics

The job uses DuckDB to process flight delay CSV files and supports AWS S3 or a
local S3-compatible service such as MinIO. It writes the average arrival delay
by year, month, and airline, plus route-level disruption insights.

The average output is written to `s3://alexp-bucket/output/teste-output.csv`;
route insights are written to
`s3://alexp-bucket/output/route-disruption-insights.csv`. Both are single CSV
objects with headers.

## Run locally

Docker Compose starts MinIO. The seed target uploads the CSV files in `input/`
to the local bucket, and the run target executes `main.py` in DuckDB.

```sh
make up
make seed
make run
make down
```

MinIO's S3 API is available at `http://localhost:9002` and its console at
`http://localhost:9003` (login: `minioadmin` / `minioadmin`). LocalStack's API
is available at `http://localhost:4566`.

## Run against AWS S3

Build the image and run it with AWS credentials (or an attached IAM role).
Set `INPUT_PATH`, `INPUT_AIRPORTS_PATH`, `INPUT_AIRLINES_PATH`, and `OUTPUT_PATH`
to override the defaults. Leave `S3_ENDPOINT_URL` unset when running on AWS.
Both `s3://` and `s3a://` paths are accepted.

## Persist results in DuckDB tables

Run `test_populate..py` to populate the `FLIGHT_DISRUPTION_AVERAGES` and
`ROUTE_DISRUPTION_INSIGHTS` tables in a persistent DuckDB database. By default,
the database file is `flight_delays.duckdb` beside the script. Set
`DUCKDB_PATH` to use a different file location; make sure that location is on
persistent storage when running in a container.
