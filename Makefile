.PHONY: build-image up seed-local-s3 seed-aws-s3 run down logs build-package

build-image: 
	docker build -t flight-delay-average:local .

up:
	docker compose up -d minio 
	docker compose run --rm -it --entrypoint /bin/bash duckdb

seed-local-s3:
	python3 seed.py

seed-aws-s3:
	aws s3 cp ./input s3://alexp-teste/input/ --recursive 

run: 
	python main.py 

down:
	docker compose down

logs:
	docker compose logs -f minio

build-package:
	pip install -r requirements.txt 
	rm -rf package/glue_etl/*
	pip wheel --no-deps -w dist . --wheel-dir package
	mkdir -p package/glue_etl
	mv package/glue_etl-*.whl package/glue_etl/app.whl
	cp requirements.txt package/glue_etl