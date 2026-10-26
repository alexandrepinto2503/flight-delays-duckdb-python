aws ec2 describe-vpc-endpoints \
	--filters Name=vpc-id,Values=vpc-014c0a7cd0c2acb73 \
	--region us-east-1 \
	--query 'VpcEndpoints[?ServiceName==`com.amazonaws.us-east-1.ecr.api` || ServiceName==`com.amazonaws.us-east-1.ecr.dkr` || ServiceName==`com.amazonaws.us-east-1.logs`].[ServiceName,State,VpcEndpointId]' \
	--output table