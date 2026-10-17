# DriveCoach: one command to deploy, one to destroy.
#   make bootstrap GITHUB_REPO=owner/repo EMAIL=you@example.com   (once)
#   make deploy
#   make upload            # field order, 5 s apart (scripts/upload_lessons.sh)
#   make results           # README tables from the live API
#   make destroy
SHELL  := /bin/bash
REGION ?= us-east-1
TF     := terraform -chdir=terraform
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
DELAY  ?= 5
QUEUE  ?= ingest
# BUDGET=false if the $$10 budget alert was already created in the console
BUDGET ?= true

.PHONY: test lint bootstrap init deploy destroy upload upload-burst results api-key replay-dlq reset clean

test:
	$(PYTHON) -m pytest -q

lint:
	terraform fmt -check -recursive terraform
	$(TF) init -backend=false -input=false >/dev/null && $(TF) validate

bootstrap:
	@test -n "$(GITHUB_REPO)" -a -n "$(EMAIL)" || (echo "usage: make bootstrap GITHUB_REPO=owner/repo EMAIL=you@example.com" && exit 1)
	terraform -chdir=terraform/bootstrap init -input=false
	terraform -chdir=terraform/bootstrap apply -var region=$(REGION) -var github_repo=$(GITHUB_REPO) -var budget_email=$(EMAIL) -var create_budget=$(BUDGET)
	@printf 'bucket = "%s"\nkey    = "drivecoach/terraform.tfstate"\nregion = "%s"\n' \
		"$$(terraform -chdir=terraform/bootstrap output -raw state_bucket)" "$(REGION)" > terraform/backend.hcl
	@echo "wrote terraform/backend.hcl"
	@echo "GitHub repo variables to set: AWS_REGION=$(REGION) TF_STATE_BUCKET=$$(terraform -chdir=terraform/bootstrap output -raw state_bucket) AWS_DEPLOY_ROLE_ARN=$$(terraform -chdir=terraform/bootstrap output -raw deploy_role_arn)"

init:
	@test -f terraform/backend.hcl || (echo "run make bootstrap first" && exit 1)
	$(TF) init -input=false -reconfigure -backend-config=backend.hcl

clean:
	find src -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf build

deploy: clean init
	$(TF) apply -input=false -auto-approve -var region=$(REGION)
	@echo "API: $$($(TF) output -raw api_url)   key: make api-key"

destroy: init
	$(TF) destroy -input=false -auto-approve -var region=$(REGION)

upload:
	./scripts/upload_lessons.sh "$$($(TF) output -raw raw_bucket)" incoming $(DELAY)

# Demo/test only: forget all processed lessons (keeps rules and initial students).
reset:
	$(PYTHON) scripts/reset_data.py "$$($(TF) output -raw raw_bucket)" --yes

# Everything at once, in random order: the Level 2 ordering test.
upload-burst:
	./scripts/upload_burst.sh "$$($(TF) output -raw raw_bucket)" incoming

api-key:
	@aws ssm get-parameter --with-decryption --name "$$($(TF) output -raw api_key_parameter)" \
		--query Parameter.Value --output text

results:
	@DRIVECOACH_API_URL="$$($(TF) output -raw api_url)" DRIVECOACH_API_KEY="$$($(MAKE) -s api-key)" \
		$(PYTHON) scripts/report.py

# Move messages from a DLQ back to its source queue once the cause is fixed.
replay-dlq:
	aws sqs start-message-move-task --source-arn "$$($(TF) output -raw $(QUEUE)_dlq_arn)"
