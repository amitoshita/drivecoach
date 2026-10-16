# ---------------------------------------------------------------
# Instructor read API: HTTP API + Lambda authorizer (x-api-key header).
# ---------------------------------------------------------------
resource "random_password" "instructor_api_key" {
  length  = 40
  special = false
}

resource "aws_ssm_parameter" "instructor_api_key" {
  name   = "/${local.name}/instructor_api_key"
  type   = "SecureString" # starter kit used a plain String with a key committed to git
  key_id = aws_kms_key.data.arn
  value  = random_password.instructor_api_key.result
}

resource "aws_apigatewayv2_api" "read" {
  name          = "${local.name}-read-api"
  protocol_type = "HTTP"
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.name}-read-api"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.read.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId"
      ip        = "$context.identity.sourceIp"
      route     = "$context.routeKey"
      path      = "$context.path"
      status    = "$context.status"
      latencyMs = "$context.responseLatency"
      authError = "$context.authorizer.error"
    })
  }
}

resource "aws_apigatewayv2_authorizer" "api_key" {
  api_id                            = aws_apigatewayv2_api.read.id
  name                              = "instructor-api-key"
  authorizer_type                   = "REQUEST"
  authorizer_uri                    = aws_lambda_function.fn["authorizer"].invoke_arn
  authorizer_payload_format_version = "2.0"
  enable_simple_responses           = true
  identity_sources                  = ["$request.header.x-api-key"]
  authorizer_result_ttl_in_seconds  = 300
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.read.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn["api"].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "read" {
  for_each           = toset(["GET /students/{id}", "GET /lessons/{id}"])
  api_id             = aws_apigatewayv2_api.read.id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.api_key.id
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["api"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.read.execution_arn}/*/*"
}

resource "aws_lambda_permission" "authorizer" {
  statement_id  = "AllowApiGatewayAuthorizer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["authorizer"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.read.execution_arn}/authorizers/${aws_apigatewayv2_authorizer.api_key.id}"
}
