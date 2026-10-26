AWS_ACCOUNT_ID=040210735166
AWS_REGION=us-east-1
REPO=flight-delay-average
IMAGE="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin \
	"$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
docker build --platform linux/amd64 -t "$IMAGE:latest" .
docker push "$IMAGE:latest"