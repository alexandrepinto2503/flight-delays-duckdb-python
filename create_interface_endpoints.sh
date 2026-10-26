REGION=us-east-1
VPC_ID=vpc-014c0a7cd0c2acb73
SUBNET_ID=subnet-0dbf78f1b9d8c6254
ENDPOINT_SG=sg-0915f71f0b78f17e4

for SERVICE in ecr.api ecr.dkr logs; do
aws ec2 create-vpc-endpoint \
		--vpc-id "$VPC_ID" \
		--vpc-endpoint-type Interface \
		--service-name "com.amazonaws.$REGION.$SERVICE" \
		--subnet-ids "$SUBNET_ID" \
		--security-group-ids "$ENDPOINT_SG" \
		--private-dns-enabled \
		--region "$REGION" \
		--query 'VpcEndpoint.{Id:VpcEndpointId,Service:ServiceName,State:State}' \
		--output table
done
