VPC_ID=vpc-014c0a7cd0c2acb73
REGION=us-east-1

aws ec2 describe-vpc-endpoints \
  --region "$REGION" \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=vpc-endpoint-type,Values=Interface" \
  --query 'VpcEndpoints[].VpcEndpointId' \
  --output text | xargs -r -n 1 aws ec2 delete-vpc-endpoints \
  --region "$REGION" \
  --vpc-endpoint-ids