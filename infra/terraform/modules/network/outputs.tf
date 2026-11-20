output "vpc_id" {
  description = "VPC ID."
  value       = aws_vpc.this.id
}

output "vpc_cidr" {
  description = "VPC CIDR block."
  value       = aws_vpc.this.cidr_block
}

output "availability_zones" {
  description = "Availability Zones in use."
  value       = local.azs
}

output "public_subnet_ids" {
  description = "Public subnet IDs (for load balancers and the NAT gateway)."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs (for databases, Redshift and Fargate tasks)."
  value       = aws_subnet.private[*].id
}

output "private_route_table_id" {
  description = "Route table shared by the private subnets."
  value       = aws_route_table.private.id
}

output "s3_gateway_endpoint_id" {
  description = "S3 gateway VPC endpoint ID."
  value       = aws_vpc_endpoint.s3.id
}

output "nat_gateway_enabled" {
  description = "Whether a NAT gateway was created."
  value       = var.enable_nat_gateway
}
