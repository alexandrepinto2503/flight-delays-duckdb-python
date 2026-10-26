import csv
import fnmatch
import os
import sys
import tempfile
import time
from urllib.parse import urlparse

import boto3
import duckdb


REQUIRED_COLUMNS = ("Year", "Month", "Reporting_Airline", "ArrDelayMinutes")
INSIGHT_COLUMNS = (
    "Origin",
    "Dest",
    "Cancelled",
    "Diverted",
    "ArrDel15",
    "DepDelayMinutes",
    "CarrierDelay",
    "WeatherDelay",
    "NASDelay",
    "SecurityDelay",
    "LateAircraftDelay",
)


def parse_s3_uri(uri):
    parsed = urlparse(uri)
    if parsed.scheme not in ("s3", "s3a") or not parsed.netloc:
        raise ValueError(f"Expected an s3:// or s3a:// URI, got: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def sql_literal(value):
    return "'" + value.replace("'", "''") + "'"


def download_s3_files(s3_client, uri, local_directory, local_prefix):
    bucket, key_pattern = parse_s3_uri(uri)
    wildcard_positions = [
        position
        for character in "*?["
        if (position := key_pattern.find(character)) != -1
    ]

    if wildcard_positions:
        prefix = key_pattern[: min(wildcard_positions)]
        paginator = s3_client.get_paginator("list_objects_v2")
        keys = (
            item["Key"]
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
            for item in page.get("Contents", [])
            if fnmatch.fnmatchcase(item["Key"], key_pattern)
        )
    else:
        keys = iter([key_pattern])

    local_paths = []
    for index, key in enumerate(keys):
        extension = os.path.splitext(key)[1]
        local_path = os.path.join(
            local_directory, f"{local_prefix}_{index:05d}{extension}"
        )
        s3_client.download_file(bucket, key, local_path)
        local_paths.append(local_path)

    if not local_paths:
        raise FileNotFoundError(f"No S3 objects matched: {uri}")
    return local_paths


def read_csv_table(connection, table_name, paths):
    path_list = ", ".join(sql_literal(path) for path in paths)
    connection.execute(
        f"CREATE TEMP TABLE {table_name} AS "
        f"SELECT * FROM read_csv([{path_list}], "
        "header=true, auto_detect=true, union_by_name=true)"
    )


def read_airlines_table(connection, paths, source_uri):
    connection.execute(
        "CREATE TEMP TABLE airlines (iata_code VARCHAR, name VARCHAR)"
    )
    for path in paths:
        with open(path, newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            fieldnames = reader.fieldnames or []
            normalized_names = {
                name.strip().lower(): name for name in fieldnames if name
            }
            code_column = normalized_names.get("iata_code")
            name_column = normalized_names.get("name")
            airline_name_column = normalized_names.get("airline_name")
            suffix_column = normalized_names.get("airline_suffix")

            if not code_column:
                raise ValueError(
                    f"Airlines input from {source_uri} ({path}) is missing "
                    f"required column 'iata_code'. Detected columns: {fieldnames}"
                )
            if not name_column and not airline_name_column:
                raise ValueError(
                    f"Airlines input from {source_uri} ({path}) is missing a name "
                    f"column (expected 'name' or 'airline_name'). "
                    f"Detected columns: {fieldnames}"
                )

            rows = []
            for row in reader:
                airline_name = row.get(name_column) if name_column else row.get(airline_name_column)
                if suffix_column and not name_column:
                    suffix = row.get(suffix_column)
                    if suffix:
                        airline_name = f"{airline_name or ''},{suffix}"
                rows.append((row.get(code_column), airline_name))

            if rows:
                connection.executemany(
                    "INSERT INTO airlines VALUES (?, ?)",
                    rows,
                )


def validate_columns(connection, table_name, required_columns, description):
    available_columns = {
        row[0] for row in connection.execute(f"DESCRIBE {table_name}").fetchall()
    }
    missing_columns = [name for name in required_columns if name not in available_columns]
    if missing_columns:
        raise ValueError(f"Input is missing {description}: {', '.join(missing_columns)}")


AVERAGES_QUERY = """
    WITH averages AS (
        SELECT Year, Month, Reporting_Airline, avg(ArrDelayMinutes) AS AvgArrDelayMinutes
        FROM flights
        GROUP BY Year, Month, Reporting_Airline
    )
    SELECT averages.Year, averages.Month, averages.Reporting_Airline,
           averages.AvgArrDelayMinutes, airlines.name
    FROM averages
    LEFT JOIN airlines ON averages.Reporting_Airline = airlines.iata_code
"""


ROUTE_INSIGHTS_QUERY = """
    WITH route_metrics AS (
        SELECT
            Year,
            Month,
            Reporting_Airline,
            Origin,
            Dest,
            count(*) AS ScheduledFlights,
            count(*) FILTER (WHERE Cancelled = 0 AND Diverted = 0) AS CompletedFlights,
            count(*) FILTER (WHERE Cancelled = 1) AS CancelledFlights,
            count(*) FILTER (WHERE Diverted = 1) AS DivertedFlights,
            count(*) FILTER (WHERE ArrDel15 = 1) AS ArrivalsDelayed15Plus,
            count(*) FILTER (WHERE ArrDel15 = 1 OR Cancelled = 1 OR Diverted = 1)
                AS DisruptedFlights,
            avg(ArrDelayMinutes) FILTER (WHERE Cancelled = 0 AND Diverted = 0)
                AS AvgArrivalDelayMinutes,
            approx_quantile(ArrDelayMinutes, 0.9)
                FILTER (WHERE Cancelled = 0 AND Diverted = 0) AS P90ArrivalDelayMinutes,
            avg(DepDelayMinutes - ArrDelayMinutes)
                FILTER (WHERE Cancelled = 0 AND Diverted = 0) AS AvgDelayRecoveredMinutes,
            avg(coalesce(CarrierDelay, 0)) AS AvgCarrierDelayMinutesPerFlight,
            avg(coalesce(WeatherDelay, 0)) AS AvgWeatherDelayMinutesPerFlight,
            avg(coalesce(NASDelay, 0)) AS AvgNASDelayMinutesPerFlight,
            avg(coalesce(SecurityDelay, 0)) AS AvgSecurityDelayMinutesPerFlight,
            avg(coalesce(LateAircraftDelay, 0)) AS AvgLateAircraftDelayMinutesPerFlight
        FROM flights
        GROUP BY Year, Month, Reporting_Airline, Origin, Dest
    ),
    route_rates AS (
        SELECT *,
            CancelledFlights * 1.0 / ScheduledFlights AS CancellationRate,
            DivertedFlights * 1.0 / ScheduledFlights AS DiversionRate,
            ArrivalsDelayed15Plus * 1.0 / NULLIF(CompletedFlights, 0)
                AS ArrivalDelayRate15Plus,
            DisruptedFlights * 1.0 / ScheduledFlights AS DisruptionRate
        FROM route_metrics
    ),
    ranked_routes AS (
        SELECT *, dense_rank() OVER (
            PARTITION BY Year, Month
            ORDER BY DisruptionRate DESC, ScheduledFlights DESC
        ) AS MonthlyDisruptionRank
        FROM route_rates
    )
    SELECT ranked_routes.*,
        origin_airports.airport AS OriginAirport,
        origin_airports.city AS OriginCity,
        origin_airports.state AS OriginState,
        destination_airports.airport AS DestAirport,
        destination_airports.city AS DestCity,
        destination_airports.state AS DestState
    FROM ranked_routes
    LEFT JOIN airports AS origin_airports ON ranked_routes.Origin = origin_airports.iata
    LEFT JOIN airports AS destination_airports ON ranked_routes.Dest = destination_airports.iata
"""


def main():
    job_name = os.getenv("JOB_NAME", "flight-delay-average")
    input_flight_delays_uri = os.getenv(
        "INPUT_PATH", "s3://alexp-teste/input/flight-delays/*.csv"
    )
    input_airports_uri = os.getenv(
        "INPUT_AIRPORTS_PATH", "s3://alexp-teste/input/airports/*.csv"
    )
    input_airlines_uri = os.getenv(
        "INPUT_AIRLINES_PATH", "s3://alexp-teste/input/airlines/*.csv"
    )
    output_uri = os.getenv("OUTPUT_PATH", "s3://alexp-teste/output/teste-output.csv")
    output_bucket, output_key = parse_s3_uri(output_uri)
    insights_output_uri = os.getenv(
        "INSIGHTS_OUTPUT_PATH",
        f"s3://{output_bucket}/output/route-disruption-insights.csv",
    )
    insights_bucket, insights_key = parse_s3_uri(insights_output_uri)
    if insights_bucket != output_bucket:
        raise ValueError("Both output files must use the same S3 bucket")

    s3_client = boto3.client("s3")
    with tempfile.TemporaryDirectory(prefix="flight-delay-glue-") as temp_directory:
        airlines_paths = download_s3_files(
            s3_client, input_airlines_uri, temp_directory, "airlines"
        )
        airports_paths = download_s3_files(
            s3_client, input_airports_uri, temp_directory, "airports"
        )
        flight_paths = download_s3_files(
            s3_client, input_flight_delays_uri, temp_directory, "flights"
        )

        average_output_path = os.path.join(temp_directory, "average-output.csv")
        insights_output_path = os.path.join(temp_directory, "route-insights.csv")
        connection = duckdb.connect()
        try:
            read_airlines_table(connection, airlines_paths, input_airlines_uri)
            read_csv_table(connection, "airports", airports_paths)
            read_csv_table(connection, "flights", flight_paths)

            validate_columns(connection, "flights", REQUIRED_COLUMNS, "required columns")
            validate_columns(
                connection,
                "flights",
                INSIGHT_COLUMNS,
                "columns required for route insights",
            )

            connection.execute(
                f"COPY ({AVERAGES_QUERY}) TO {sql_literal(average_output_path)} "
                "(HEADER, FORMAT CSV)"
            )
            connection.execute(
                f"COPY ({ROUTE_INSIGHTS_QUERY}) TO {sql_literal(insights_output_path)} "
                "(HEADER, FORMAT CSV)"
            )
        finally:
            connection.close()

        s3_client.upload_file(average_output_path, output_bucket, output_key)
        print(f"Wrote s3://{output_bucket}/{output_key}")
        s3_client.upload_file(insights_output_path, insights_bucket, insights_key)
        print(f"Wrote s3://{insights_bucket}/{insights_key}")
        print(f"Completed DuckDB job: {job_name}")


if __name__ == "__main__":
    start_time = time.perf_counter()
    try:
        main()
    except Exception as error:
        print(f"Job failed: {error}", file=sys.stderr)
        raise
    finally:
        elapsed_minutes, elapsed_seconds = divmod(time.perf_counter() - start_time, 60)
        print(
            f"Total elapsed time: {int(elapsed_minutes)} minutes "
            f"and {elapsed_seconds:.2f} seconds"
        )
