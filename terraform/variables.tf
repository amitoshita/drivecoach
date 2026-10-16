variable "region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "owner" {
  description = "Your name, used for tagging"
  type        = string
  default     = "candidate"
}

variable "project_name" {
  description = "Prefix for resource names"
  type        = string
  default     = "drivecoach"
}

# The starter kit had a hardcoded default API key here. It is in git history,
# so it is treated as leaked: the key is now generated at deploy time
# (random_password in api.tf) and stored only as an SSM SecureString.

variable "alarm_email" {
  description = "Email that receives CloudWatch alarm notifications (empty = no subscription)"
  type        = string
  default     = ""
}

variable "force_destroy" {
  description = "Allow `terraform destroy` to delete non-empty buckets. True for this assignment; false in production."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention"
  type        = number
  default     = 30
}
