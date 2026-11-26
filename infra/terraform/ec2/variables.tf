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
  description = "Public Git repository the server clones and builds."
  type        = string
  default     = "https://github.com/dunascode-prog/voice_agent_prototype.git"
}

variable "instance_type" {
  description = "t3.small (2 vCPU burstable, 2 GB + 2 GB swap) is enough for a few concurrent demo calls. t3.medium for more headroom."
  type        = string
  default     = "t3.small"
}

variable "volume_size_gb" {
  type    = number
  default = 20
}

variable "llm_model" {
  description = "Claude model on Bedrock, without the inference-profile prefix (us. is added)."
  type        = string
  default     = "claude-sonnet-4-6"
}
