import os
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def main():
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://minio:9000"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )
    bucket = os.getenv("S3_BUCKET", "alexp-teste")

    for attempt in range(60):
        try:
            client.head_bucket(Bucket=bucket)
            break
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status not in (403, 404):
                raise
            if status == 403:
                break
            try:
                client.create_bucket(Bucket=bucket)
            except ClientError as create_error:
                code = create_error.response.get("Error", {}).get("Code")
                if code not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
                    raise
            break
        except Exception:
            if attempt == 59:
                raise
            time.sleep(2)

    input_dir = Path(os.getenv("INPUT_DIR", "/seed"))
    datasets = {
        "flight-delays": "input/flight-delays",
        "airports": "input/airports",
        "airlines": "input/airlines",
    }
    uploaded_files = 0
    for source_name, destination_prefix in datasets.items():
        source_dir = input_dir / source_name
        if not source_dir.is_dir():
            raise RuntimeError(f"Input directory not found: {source_dir}")
        csv_files = sorted(source_dir.rglob("*.csv"))
        if not csv_files:
            raise RuntimeError(f"No CSV files found in {source_dir}")

        for path in csv_files:
            relative_path = path.relative_to(source_dir).as_posix()
            object_key = f"{destination_prefix}/{relative_path}"
            client.upload_file(str(path), bucket, object_key)
            print(f"Uploaded {path.name} to s3://{bucket}/{object_key}")
            uploaded_files += 1

    if not uploaded_files:
        raise RuntimeError(f"No CSV files found under {input_dir}")


if __name__ == "__main__":
    main()