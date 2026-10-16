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

variable "instructor_api_key" {
  description = "API key instructors use to call the read API"
  type        = string
  default     = "dc-instr-2026-Kx9q7Lm2"
}
