# Kubernetes Self-Healing Platform: the portable core on kind, and the AWS
# module on EKS.
#
#   make up        kind + Cilium + every platform chart + bootstrap (zero spend)
#   make demo      baseline -> hardened, every row; writes evidence/<run-id>/
#   make down      delete the kind cluster
#
#   EKS (docs/runbook-aws.md): make eks-plan, then the operator applies the
#   saved plan; make eks-up; make demo TARGET=eks; teardown in four steps.
#
# The runner exits non-zero if any hardened row misses its threshold or
# cannot be judged. It never skips a row.
#
# Prereqs: docker, kind, kubectl, helm, cilium, hubble, jq, openssl 3.4+, python3.

SHELL := /usr/bin/env bash
KIND_CLUSTER := selfheal
# A kubeconfig of its own per target: other clusters on this machine (and other
# sessions switching the shared current-context) can never receive a lab command.
TARGET ?= kind
ifeq ($(TARGET),eks)
export KUBECONFIG := $(CURDIR)/.kube/eks
else
export KUBECONFIG := $(CURDIR)/.kube/config
endif

# Pinned chart versions (app version in the comment). Verified 2026-11-03.
CILIUM_VERSION          := 1.20.4
METRICS_SERVER_VERSION  := 3.14.0   # 0.9.0
KPS_VERSION             := 92.17.1  # operator v0.94.2
CERT_MANAGER_VERSION    := v1.21.3
ESO_VERSION             := 2.14.0   # v2.14.0, API external-secrets.io/v1
RELOADER_VERSION        := 2.2.19   # v1.4.23
ROLLOUTS_VERSION        := 2.43.8   # v1.10.1
ARGOCD_VERSION          := 10.11.7  # v3.5.7
CHAOS_MESH_VERSION      := 2.8.7
FALCO_VERSION           := 9.2.2    # 0.45.2, Falcosidekick 2.35.1
TALON_VERSION           := 0.5.0    # app 0.3.0 (alpha, pinned; actionner kubernetes:label)
SLOTH_IMAGE             := ghcr.io/slok/sloth:v0.13.0
K6_IMAGE                := grafana/k6:2.3.0
KARPENTER_VERSION       := 1.15.1   # EKS only; NodeRepair gate on (ADR-0005)

HELM := helm

.DEFAULT_GOAL := help

.PHONY: help
help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

# --- cluster ----------------------------------------------------------------

.PHONY: up
up: kind-up platform bootstrap ## Everything: cluster, platform, bootstrap

.PHONY: kind-up
kind-up: ## Create the kind cluster, install pinned Cilium + Hubble, load the lab images
	mkdir -p .kube
	kind create cluster --config kind/cluster.yaml --kubeconfig $(KUBECONFIG)
	$(MAKE) repos
	$(HELM) upgrade --install cilium cilium/cilium --version $(CILIUM_VERSION) -n kube-system -f cilium/values.yaml
	cilium status --wait --wait-duration 6m >/dev/null
	cilium/readiness-gate.sh
	$(MAKE) images

.PHONY: images
images: ## Build and load the app (v1, v2) and Git server images
	docker build -q -t selfheal-app:v1 --build-arg RELEASE=v1 app
	docker build -q -t selfheal-app:v2 --build-arg RELEASE=v2 app
	docker build -q -t selfheal-gitserver:dev app/gitserver
	docker pull -q $(K6_IMAGE) >/dev/null
	kind load docker-image selfheal-app:v1 selfheal-app:v2 selfheal-gitserver:dev $(K6_IMAGE) --name $(KIND_CLUSTER)

.PHONY: repos
repos:
	@helm repo add cilium https://helm.cilium.io/ >/dev/null 2>&1 || true
	@helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null 2>&1 || true
	@helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
	@helm repo add jetstack https://charts.jetstack.io >/dev/null 2>&1 || true
	@helm repo add external-secrets https://charts.external-secrets.io >/dev/null 2>&1 || true
	@helm repo add stakater https://stakater.github.io/stakater-charts >/dev/null 2>&1 || true
	@helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
	@helm repo add chaos-mesh https://charts.chaos-mesh.org >/dev/null 2>&1 || true
	@helm repo add falcosecurity https://falcosecurity.github.io/charts >/dev/null 2>&1 || true
	@helm repo update >/dev/null

.PHONY: platform
platform: repos ## Install every platform chart (order matters: Prometheus CRDs first)
	$(HELM) upgrade --install metrics-server metrics-server/metrics-server --version $(METRICS_SERVER_VERSION) \
		-n kube-system -f platform/metrics-server.yaml --wait
	$(HELM) upgrade --install kps prometheus-community/kube-prometheus-stack --version $(KPS_VERSION) \
		-n monitoring --create-namespace -f platform/kube-prometheus-stack.yaml --wait --timeout 10m
	$(HELM) upgrade --install cert-manager jetstack/cert-manager --version $(CERT_MANAGER_VERSION) \
		-n cert-manager --create-namespace -f platform/cert-manager.yaml --wait
	$(HELM) upgrade --install external-secrets external-secrets/external-secrets --version $(ESO_VERSION) \
		-n external-secrets --create-namespace -f platform/external-secrets.yaml --wait
	$(HELM) upgrade --install argo-rollouts argo/argo-rollouts --version $(ROLLOUTS_VERSION) \
		-n argo-rollouts --create-namespace -f platform/argo-rollouts.yaml --wait
	# Reloader after Rollouts: its chart grants RBAC on rollouts.argoproj.io only
	# if that API exists at install time. Installed first, it silently gets no
	# permission and never restarts a Rollout (found in run 1, row S1).
	$(HELM) upgrade --install reloader stakater/reloader --version $(RELOADER_VERSION) \
		-n reloader --create-namespace -f platform/reloader.yaml --wait
	$(HELM) upgrade --install argocd argo/argo-cd --version $(ARGOCD_VERSION) \
		-n argocd --create-namespace -f platform/argo-cd.yaml --wait --timeout 10m
	$(HELM) upgrade --install chaos-mesh chaos-mesh/chaos-mesh --version $(CHAOS_MESH_VERSION) \
		-n chaos-mesh --create-namespace -f platform/chaos-mesh.yaml --wait
	$(HELM) upgrade --install falco-talon falcosecurity/falco-talon --version $(TALON_VERSION) \
		-n falco --create-namespace -f platform/falco-talon.yaml \
		--set-file config.rulesOverride=platform/talon/baseline.yaml --wait
	$(HELM) upgrade --install falco falcosecurity/falco --version $(FALCO_VERSION) \
		-n falco -f platform/falco.yaml --wait --timeout 10m

.PHONY: bootstrap
bootstrap: ## CA, backend key, partner keys, baseline cert, seed Git, Argo CD root app
	scripts/bootstrap.sh

.PHONY: down
down: ## Delete the kind cluster
	kind delete cluster --name $(KIND_CLUSTER) --kubeconfig $(KUBECONFIG)

# --- the experiment ---------------------------------------------------------

.PHONY: demo
demo: ## Run every row baseline then hardened; evidence/<run-id>/report.md (+ .json). TARGET=eks for EKS
	$(if $(filter eks,$(TARGET)),AWS_PROFILE=$(LAB_PROFILE) MGMT_PROFILE=$(MGMT_PROFILE),) \
	TALON_VERSION=$(strip $(TALON_VERSION)) python3 scenarios/run.py --target $(TARGET) \
		$(if $(RUN_ID),--run-id $(RUN_ID),) $(if $(ROWS),--rows $(ROWS),)

.PHONY: profile
profile: ## Switch profile by hand for exploration: make profile P=baseline|hardened
	python3 scenarios/run.py --only-profile $(P)

.PHONY: seed
seed: ## Re-seed the in-cluster Git server from ./gitops
	scripts/seed-git.sh

# --- static gates (no cluster) ---------------------------------------------

.PHONY: slo
slo: ## Regenerate gitops/base/slo-rules.yaml from slo/ with Sloth
	docker run --rm -v "$(CURDIR)/slo:/slo" $(SLOTH_IMAGE) generate -i /slo/web.yaml \
		--slo-period-windows-path=/slo/windows --default-slo-period=60h 2>/dev/null \
		| python3 scripts/slo-wrap.py > gitops/base/slo-rules.yaml

.PHONY: test
test: ## Unit-test the runner's honesty rules and the R2 responder's refusals (no cluster, no AWS)
	cd scenarios && python3 -m unittest -v test_classify
	cd infra/aws/responder && python3 -m unittest -v test_handler

.PHONY: check
check: test eks-check ## Static checks: every profile renders (kind and EKS), shellcheck, Terraform gates, pre-commit
	kubectl kustomize gitops/profiles/baseline >/dev/null
	kubectl kustomize gitops/profiles/hardened >/dev/null
	scripts/render-eks-profiles.sh
	shellcheck scripts/*.sh cilium/*.sh infra/aws/rotation/build.sh
	python3 -m py_compile app/app.py app/dbprobe/dbprobe.py scenarios/run.py infra/aws/responder/handler.py
	pre-commit run --all-files

# --- EKS: the AWS module ------------------------------------------------------
# The credentialed apply and destroy are run by the operator from the saved
# plans these targets print, so a human reads every plan first. Nothing here
# applies or destroys infrastructure.
#
#   MGMT_PROFILE  credentials Terraform starts from (assumes into the lab and
#                 security accounts; infra/aws/main.tf)
#   LAB_PROFILE   credentials for the lab account (AWS CLI, runner)

TFA := AWS_PROFILE=$(MGMT_PROFILE) terraform -chdir=$(CURDIR)/infra/aws
MY_IP_CIDRS = -var='cluster_public_access_cidrs=["'"$$(curl -4 -s ifconfig.me)"'/32"]'
CHART_VERSIONS = CILIUM_VERSION=$(strip $(CILIUM_VERSION)) KARPENTER_VERSION=$(strip $(KARPENTER_VERSION)) \
	METRICS_SERVER_VERSION=$(strip $(METRICS_SERVER_VERSION)) KPS_VERSION=$(strip $(KPS_VERSION)) \
	CERT_MANAGER_VERSION=$(strip $(CERT_MANAGER_VERSION)) ESO_VERSION=$(strip $(ESO_VERSION)) \
	ROLLOUTS_VERSION=$(strip $(ROLLOUTS_VERSION)) RELOADER_VERSION=$(strip $(RELOADER_VERSION)) \
	ARGOCD_VERSION=$(strip $(ARGOCD_VERSION)) CHAOS_MESH_VERSION=$(strip $(CHAOS_MESH_VERSION)) \
	TALON_VERSION=$(strip $(TALON_VERSION)) FALCO_VERSION=$(strip $(FALCO_VERSION))
EKS_ENV = MGMT_PROFILE=$(MGMT_PROFILE) LAB_PROFILE=$(LAB_PROFILE) KUBECONFIG=$(CURDIR)/.kube/eks

.PHONY: eks-check
eks-check: ## Terraform gates (no credentials): fmt, validate, tflint, checkov, trivy, conftest
	terraform -chdir=infra/aws fmt -check -recursive
	infra/aws/rotation/build.sh >/dev/null
	terraform -chdir=infra/aws init -backend=false -input=false >/dev/null
	terraform -chdir=infra/aws validate
	tflint --chdir=infra/aws --recursive --minimum-failure-severity=warning
	checkov -d infra/aws --framework terraform --quiet --compact --skip-path .terraform --baseline infra/aws/.checkov.baseline
	trivy config --quiet --severity CRITICAL,HIGH --exit-code 1 --skip-dirs infra/aws/.terraform --skip-dirs infra/aws/build infra/aws
	conftest test --parser hcl2 --combine --policy ../platform-guardrails/policy infra/aws/*.tf infra/aws/modules/*/*.tf

.PHONY: eks-lock
eks-lock: ## Record provider hashes for CI (linux_amd64) and this Mac (darwin_arm64)
	terraform -chdir=infra/aws providers lock -platform=linux_amd64 -platform=darwin_arm64

.PHONY: eks-cost
eks-cost: ## Infracost estimate of the full stack (before any live session)
	infra/aws/rotation/build.sh >/dev/null
	infracost breakdown --path infra/aws --terraform-var-file lab.auto.tfvars --show-skipped

.PHONY: eks-preflight
eks-preflight: ## Read-only checks: accounts, Config recorder, GuardDuty admin, leftovers, quota, IPv4
	scripts/eks-preflight.sh

.PHONY: eks-plan
eks-plan: eks-preflight ## Write infra/aws/tfplan (API scoped to your IPv4 /32) and print the apply command
	infra/aws/rotation/build.sh
	$(TFA) init -input=false
	$(TFA) plan -input=false -out=tfplan $(MY_IP_CIDRS)
	@echo; echo "Read the plan, then run:  AWS_PROFILE=$(MGMT_PROFILE) terraform -chdir=$(CURDIR)/infra/aws apply $(CURDIR)/infra/aws/tfplan"

.PHONY: eks-platform
eks-platform: ## Kubeconfig, Cilium ENI, Karpenter, platform charts, responder RBAC (after the apply)
	$(EKS_ENV) $(CHART_VERSIONS) scripts/eks-platform.sh

.PHONY: eks-images
eks-images: ## Build linux/amd64 images, push the git SHA tags to ECR, write the generated overlay
	$(EKS_ENV) scripts/eks-images.sh

.PHONY: eks-bootstrap
eks-bootstrap: ## CA, partner key in Secrets Manager, S1b DB user and rotation, Git, Argo CD root app
	$(EKS_ENV) scripts/eks-bootstrap.sh

.PHONY: eks-up
eks-up: eks-platform eks-images eks-bootstrap ## Everything after the apply, in order

.PHONY: eks-runtime-off-plan
eks-runtime-off-plan: ## Teardown 1 of 4: plan turning GuardDuty Runtime Monitoring off, print the apply command
	$(TFA) plan -input=false -out=runtime-off.tfplan -var=runtime_monitoring=false $(MY_IP_CIDRS)
	@echo; echo "Read the plan, then run:  AWS_PROFILE=$(MGMT_PROFILE) terraform -chdir=$(CURDIR)/infra/aws apply $(CURDIR)/infra/aws/runtime-off.tfplan"
	@echo "Then:  make eks-predestroy"

.PHONY: eks-predestroy
eks-predestroy: ## Teardown 2 of 4: Karpenter nodes, protection, Cilium, GuardDuty leftovers, snapshots
	$(EKS_ENV) scripts/eks-predestroy.sh

.PHONY: eks-destroy-plan
eks-destroy-plan: ## Teardown 3 of 4: write infra/aws/destroy.tfplan and print the command
	infra/aws/rotation/build.sh >/dev/null
	$(TFA) plan -destroy -input=false -out=destroy.tfplan -var=runtime_monitoring=false $(MY_IP_CIDRS)
	@echo; echo "Read the plan, then run:  AWS_PROFILE=$(MGMT_PROFILE) terraform -chdir=$(CURDIR)/infra/aws apply $(CURDIR)/infra/aws/destroy.tfplan"
	@echo "Then:  make eks-verify-gone"

.PHONY: eks-verify-gone
eks-verify-gone: ## Teardown 4 of 4: read-only proof nothing survived (every teardown gotcha)
	LAB_PROFILE=$(LAB_PROFILE) scripts/eks-verify-gone.sh
