variable "public_key_location" {}
variable "private_key_location" {}
variable "owner" {}
variable "access_key" {}
variable "secret_key" {}
variable "region" {}
variable "availability_zone" {}
variable "subnet" {}
variable "vpc" {}
variable "server_count" { type = number }
variable "server_instance_type" {}
variable "server_user" {}
variable "server_ami" {}
variable "monitoring_instance_type" {}
variable "monitoring_ami" {}
variable "client_instance_type" {}
variable "client_count" { type = number }
variable "client_user" {}
variable "client_ami" {}
variable "deployment_name" {}

locals {
  deployment_name       = var.deployment_name

  region                = var.region
  availability_zone     = var.availability_zone
  subnet                = var.subnet
  vpc                   = var.vpc
  server_count          = var.server_count
  server_instance_type  = var.server_instance_type
  server_user           = var.server_user
  server_ami            = var.server_ami

  monitoring_instance_type = var.monitoring_instance_type
  monitoring_ami           = var.monitoring_ami

  client_instance_type = var.client_instance_type
  client_count         = var.client_count
  client_user          = var.client_user
  client_ami           = var.client_ami
}

resource "random_id" "deployment_id" {
  byte_length = 8
}

output "deployment_id" {
  value = random_id.deployment_id.hex
}

provider "aws" {
  access_key = var.access_key
  secret_key = var.secret_key
  region  = local.region
}

resource "aws_key_pair" "keypair" {
  key_name   = "sso-keypair-${local.deployment_name}"
  public_key = file(var.public_key_location)
}

# ========== servers ==========================

resource "aws_security_group" "server-sg" {
  name        = "sso-server-sg-${local.deployment_name}"
  description = "Security group for Scylla servers"
  vpc_id      = local.vpc

  # list of ports https://docs.scylladb.com/operating-scylla/admin/

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "CQL"
    from_port   = 9042
    to_port     = 9042
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSL_CQL"
    from_port   = 9142
    to_port     = 9142
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "RPC"
    from_port   = 7000
    to_port     = 7000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSL RPC"
    from_port   = 7001
    to_port     = 7001
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "JMX"
    from_port   = 7199
    to_port     = 7199
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "REST"
    from_port   = 10000
    to_port     = 10000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "NodeExporter"
    from_port   = 9100
    to_port     = 9100
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Prometheus"
    from_port   = 9180
    to_port     = 9180
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Thrift"
    from_port   = 9160
    to_port     = 9160
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "terraform",
    keep = "alive"
  }
}

resource "aws_spot_instance_request" "server" {
  key_name          = aws_key_pair.keypair.key_name
  ami               = local.server_ami
  instance_type     = local.server_instance_type
  count             = local.server_count
  availability_zone = local.availability_zone
  subnet_id         = local.subnet
  wait_for_fulfillment = true
  spot_type = "one-time"

  tags = {
    Name = "sso-server-${count.index}-${local.deployment_name}"
    keep = "alive"
    Owner = var.owner
  }

  vpc_security_group_ids = [
    aws_security_group.server-sg.id
  ]

  ebs_block_device {
    device_name = "/dev/sda1"
    volume_type = "gp3"
    volume_size = 100
  }

  # Terraform reprovisions the scylla node even when its configuration hasn't changed. This workaround prevents that.
  lifecycle {
      ignore_changes = [ebs_block_device]
  }

  user_data = jsonencode({
    start_scylla_on_first_boot = false
  })
}

output "server_public_ips" {
  value = aws_spot_instance_request.server.*.public_ip
}

output "server_private_ips" {
  value = aws_spot_instance_request.server.*.private_ip
}

# ========== monitoring ==========================

resource "aws_security_group" "monitoring-sg" {
  name        = "sso-monitoring-sg-${local.deployment_name}"
  description = "Security group for Prometheus"
  vpc_id      = local.vpc

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "monitoring"
    from_port   = 9090
    to_port     = 9090
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "x"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Alert Manager"
    from_port   = 9003
    to_port     = 9003
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "terraform",
    keep = "alive"
  }
}

resource "aws_instance" "monitoring" {
  key_name          = aws_key_pair.keypair.key_name
  ami               = local.monitoring_ami
  instance_type     = local.monitoring_instance_type
  availability_zone = local.availability_zone
  subnet_id         = local.subnet

  tags = {
    Name = "sso-monitoring-${local.deployment_name}"
    keep = "alive"
    Owner = var.owner
  }

  vpc_security_group_ids = [
    aws_security_group.monitoring-sg.id
  ]

  lifecycle {
      ignore_changes = [ebs_block_device]
  }

  ebs_block_device {
      device_name = "/dev/sda1"
      volume_type = "gp3"
      volume_size = 384
  }
}

output "monitoring_public_ips" {
  value = aws_instance.monitoring.*.public_ip
}

output "monitoring_private_ips" {
  value = aws_instance.monitoring.*.private_ip
}

# ========== clients ==========================

resource "aws_security_group" "client-sg" {
  name        = "sso-client-sg-${local.deployment_name}"
  description = "Security group for Scylla clients"
  vpc_id      = local.vpc

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "cassandra-stress-daemon"
    from_port   = 2159
    to_port     = 2159
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "terraform",
    keep = "alive"
  }
}

resource "aws_spot_instance_request" "client" {
  key_name          = aws_key_pair.keypair.key_name
  ami               = local.client_ami
  instance_type     = local.client_instance_type
  count             = local.client_count
  availability_zone = local.availability_zone
  subnet_id         = local.subnet
  wait_for_fulfillment = true
  spot_type = "one-time"

  tags = {
    Name = "sso-client-${count.index}-${local.deployment_name}",
    keep = "alive"
    Owner = var.owner
  }

  vpc_security_group_ids = [
    aws_security_group.client-sg.id
  ]
}

output "client_public_ips" {
  value = aws_spot_instance_request.client.*.public_ip
}

output "client_private_ips" {
  value = aws_spot_instance_request.client.*.private_ip
}
