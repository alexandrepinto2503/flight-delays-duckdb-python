### To query data after populating the DuckDB tables (via CLI or Web UI): 
### duckdb -ui flight_delays.duckdb

import os
import sys
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


def configure_s3(connection):
    connection.execute("LOAD httpfs")
    region = os.getenv("AWS_REGION", "us-east-1")
    connection.execute(f"SET s3_region={sql_literal(region)}")

    endpoint_url = os.getenv("S3_ENDPOINT_URL")
    if endpoint_url:
        endpoint = urlparse(endpoint_url)
        if not endpoint.hostname:
            raise ValueError(f"Invalid S3_ENDPOINT_URL: {endpoint_url}")
        connection.execute(f"SET s3_endpoint={sql_literal(endpoint.netloc)}")
        connection.execute("SET s3_url_style='path'")
        use_ssl = "true" if endpoint.scheme == "https" else "false"
        connection.execute(f"SET s3_use_ssl={use_ssl}")

    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials found for S3 access")
    frozen_credentials = credentials.get_frozen_credentials()
    connection.execute(
        f"SET s3_access_key_id={sql_literal(frozen_credentials.access_key)}"
    )
    connection.execute(
        f"SET s3_secret_access_key={sql_literal(frozen_credentials.secret_key)}"
    )
    if frozen_credentials.token:
        connection.execute(
            f"SET s3_session_token={sql_literal(frozen_credentials.token)}"
        )


def read_csv_table(connection, table_name, uri):
    normalized_uri = uri.replace("s3a://", "s3://", 1)
    connection.execute(
        f"CREATE TABLE {table_name} AS "
        f"SELECT * FROM read_csv({sql_literal(normalized_uri)}, "
        "header=true, auto_detect=true, union_by_name=true)"
    )


def read_airlines_table(connection, uri):
    normalized_uri = uri.replace("s3a://", "s3://", 1)
    connection.execute(
        """
        CREATE TABLE airlines AS
        SELECT
            iata_code,
            airline_name || coalesce(',' || airline_suffix, '') AS name
        FROM read_csv(
            ?,
            header=true,
            delim=',',
            quote='"',
            auto_detect=false,
            columns={
                'iata_code': 'VARCHAR',
                'airline_name': 'VARCHAR',
                'airline_suffix': 'VARCHAR'
            },
            strict_mode=false,
            null_padding=true
        )
        """,
        [normalized_uri],
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

    print ("Breakpoint 1")

    job_name = os.getenv("JOB_NAME", "flight-delay-average")
    input_flight_delays_uri = os.getenv(
        "INPUT_PATH", "s3://alexp-teste/input/flight-delays/*.csv"
    )
    input_airports_uri = os.getenv("INPUT_AIRPORTS_PATH", "s3://alexp-teste/input/airports/*.csv")
    input_airlines_uri = os.getenv("INPUT_AIRLINES_PATH", "s3://alexp-teste/input/airlines/*.csv")
    database_path = os.getenv(
        "DUCKDB_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "flight_delays.duckdb"),
    )

    print ("Breakpoint 2")

    connection = duckdb.connect(database_path)

    print ("Breakpoint 3")

    try:

        print ("Breakpoint 4")

        configure_s3(connection)

        print ("Breakpoint 5")

        read_airlines_table(connection, input_airlines_uri)

        print ("Breakpoint 6")
        read_csv_table(connection, "airports", input_airports_uri)

        print ("Breakpoint 7")
        read_csv_table(connection, "flights", input_flight_delays_uri)

        print ("Breakpoint 8")

        validate_columns(connection, "flights", REQUIRED_COLUMNS, "required columns")
        validate_columns(
            connection, "flights", INSIGHT_COLUMNS, "columns required for route insights"
        )

        print ("Breakpoint 9")

        connection.execute(
            f"CREATE OR REPLACE TABLE FLIGHT_DISRUPTION_AVERAGES AS ({AVERAGES_QUERY})"
        )
        print(f"Wrote FLIGHT_DISRUPTION_AVERAGES")

        connection.execute(
            f"CREATE OR REPLACE TABLE ROUTE_DISRUPTION_INSIGHTS AS ({ROUTE_INSIGHTS_QUERY})"
        )
        print(f"Wrote ROUTE_DISRUPTION_INSIGHTS")
        print(f"Completed DuckDB job: {job_name}")
    finally:
        connection.close()


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