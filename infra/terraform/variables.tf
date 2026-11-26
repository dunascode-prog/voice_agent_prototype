variable "name" {
  description = "Prefix for every resource name."
  type        = string
  default     = "voice-agent"
}

variable "region" {
  description = "AWS region. us-east-1 sits next to Twilio's US1 media edge (Ashburn)."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile Terraform runs as (needs admin rights). Empty = default credential chain."
  type        = string
  default     = ""
}

variable "git_repo_url" {
  description = "Public Git repository CodeBuild clones to build the image."
  type        = string
  default     = "https://github.com/dunascode-prog/voice_agent_prototype.git"
}

variable "image_tag" {
  description = "ECR image tag the services run. scripts/aws/deploy.py pushes both <sha> and latest."
  type        = string
  default     = "latest"
}

variable "llm_model" {
  description = "Claude model on Bedrock, without the inference-profile prefix (us. is added)."
  type        = string
  default     = "claude-sonnet-4-6"
}

variable "desired_count" {
  description = "Tasks per service. Set to 0 to pause compute between demos (RDS, Redis and the ALB keep billing)."
  type        = number
  default     = 1
}

variable "gateway_cpu" {
  type    = number
  default = 256
}

variable "gateway_memory" {
  type    = number
  default = 512
}

variable "agent_cpu" {
  description = "Agent task CPU units. VAD and smart-turn run on CPU, so keep at least 1 vCPU."
  type        = number
  default     = 1024
}

variable "agent_memory" {
  type    = number
  default = 2048
}

variable "agent_deregistration_delay_s" {
  description = "How long the ALB lets live calls drain from an old agent task during a deploy. Production: 1800 (max call length)."
  type        = number
  default     = 120
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "log_retention_days" {
  type    = number
  default = 14
}
