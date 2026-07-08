#!/bin/bash

set -e

cd "$(dirname "$0")"

TAG=$1
ENV=$2

if [ -z "$TAG" ]; then
    echo "Error: No tag provided."
    echo "Usage: ./deploy_dev.sh <tag> [env]"
    exit 1
fi

if [ -z "$ENV" ]; then
    ENV="dev"
fi

CLUSTER="ai-shipping-labs"
DEPLOY_HOST=""

if [ "${ENV}" = "dev" ]; then
    DEPLOY_HOST="https://dev.aishippinglabs.com"
elif [ "${ENV}" = "prod" ]; then
    DEPLOY_HOST="https://aishippinglabs.com"
fi

predeploy_migrate_check_enabled() {
    case "${PREDEPLOY_MIGRATE_CHECK_ENABLED:-}" in
        1|true|TRUE|yes|YES|on|ON)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# Diagnostics for failed deploys — surfaces the real reason a steady-state
# wait timed out instead of "service did not stabilize" with no detail.
diagnose_service_failure() {
    local SERVICE=$1
    local ROLE=$2

    echo "--- Recent ECS service events ---"
    aws ecs describe-services \
        --cluster ${CLUSTER} \
        --services ${SERVICE} \
        --query 'services[0].events[:20]' \
        --output table || true

    echo "--- RUNNING task statuses ---"
    local RUNNING_ARNS
    RUNNING_ARNS=$(aws ecs list-tasks \
        --cluster ${CLUSTER} \
        --service-name ${SERVICE} \
        --desired-status RUNNING \
        --query 'taskArns[:5]' \
        --output text 2>/dev/null || true)
    if [ -n "${RUNNING_ARNS}" ]; then
        aws ecs describe-tasks \
            --cluster ${CLUSTER} \
            --tasks ${RUNNING_ARNS} \
            --query 'tasks[].{task:taskArn,last:lastStatus,health:healthStatus,containers:containers[].{name:name,last:lastStatus,health:healthStatus,reason:reason}}' \
            --output json || true
    else
        echo "(no RUNNING tasks)"
    fi

    echo "--- Recent STOPPED task reasons ---"
    local STOPPED_ARNS
    STOPPED_ARNS=$(aws ecs list-tasks \
        --cluster ${CLUSTER} \
        --service-name ${SERVICE} \
        --desired-status STOPPED \
        --query 'taskArns[:5]' \
        --output text 2>/dev/null || true)
    if [ -n "${STOPPED_ARNS}" ]; then
        aws ecs describe-tasks \
            --cluster ${CLUSTER} \
            --tasks ${STOPPED_ARNS} \
            --query 'tasks[].{task:taskArn,last:lastStatus,stoppedReason:stoppedReason,containers:containers[].{name:name,last:lastStatus,exitCode:exitCode,reason:reason}}' \
            --output json || true
    else
        echo "(no STOPPED tasks)"
    fi

    if [ "${ROLE}" = "worker" ]; then
        return 0
    fi

    echo "--- ALB target-group health ---"
    local TG_ARN
    TG_ARN=$(aws ecs describe-services \
        --cluster ${CLUSTER} \
        --services ${SERVICE} \
        --query 'services[0].loadBalancers[0].targetGroupArn' \
        --output text 2>/dev/null || true)
    if [ -n "${TG_ARN}" ] && [ "${TG_ARN}" != "None" ]; then
        aws elbv2 describe-target-health \
            --target-group-arn ${TG_ARN} \
            --query 'TargetHealthDescriptions[].[Target.Id,Target.Port,TargetHealth.State,TargetHealth.Reason,TargetHealth.Description]' \
            --output table || true
    else
        echo "(service has no load balancer attached)"
    fi
}

# Register a new task definition for SERVICE at the current TAG/ROLE.
# Sets the global REGISTERED_TASK_DEF_ARN to the new ARN. Runs in the main
# shell (NOT a subshell) so any `exit 1` here aborts the whole deploy loudly.
register_task_def() {
    local SERVICE=$1
    local ROLE=$2

    echo ""
    echo "=== Registering task definition for ${SERVICE} (role=${ROLE}) with tag ${TAG} ==="

    local FILE_IN="${SERVICE}-${TAG}.json"
    local FILE_OUT="updated_${SERVICE}-${TAG}.json"

    echo "Fetching task definition currently used by ${SERVICE}..."
    local CURRENT_TASK_DEF_ARN
    CURRENT_TASK_DEF_ARN=$(aws ecs describe-services \
        --cluster ${CLUSTER} \
        --services ${SERVICE} \
        --query 'services[0].taskDefinition' \
        --output text)

    if [ -z "${CURRENT_TASK_DEF_ARN}" ] || [ "${CURRENT_TASK_DEF_ARN}" = "None" ]; then
        echo "Error: Could not determine the active task definition for ${SERVICE}."
        exit 1
    fi

    aws ecs describe-task-definition \
        --task-definition ${CURRENT_TASK_DEF_ARN} \
        > ${FILE_IN}

    echo "Updating task definition with new image tag..."
    python update_task_def.py ${FILE_IN} ${TAG} ${FILE_OUT} ${ENV} ${ROLE}

    echo "Registering new task definition..."
    local NEW_TASK_DEF_ARN
    NEW_TASK_DEF_ARN=$(aws ecs register-task-definition \
        --cli-input-json file://${FILE_OUT} \
        --query 'taskDefinition.taskDefinitionArn' \
        --output text)

    rm -f ${FILE_IN} ${FILE_OUT}

    if [ -z "${NEW_TASK_DEF_ARN}" ] || [ "${NEW_TASK_DEF_ARN}" = "None" ]; then
        echo "Error: Task definition registration did not return an ARN."
        exit 1
    fi

    REGISTERED_TASK_DEF_ARN="${NEW_TASK_DEF_ARN}"
}

# Issue #1141 Phase 2A — pre-deploy gate. Disabled by default until
# DataTalksClub/aws-infra#12 grants ecs:RunTask to the deploy role. Enable by
# setting PREDEPLOY_MIGRATE_CHECK_ENABLED=true in the deploy environment.
#
# Run migrate + `check --fail-level
# ERROR` ONCE in a one-off ECS task (BOOT_MODE=predeploy) BEFORE any service
# is rolled, using the SAME registered task-def ARN that the service will run
# (same image, secrets, DEBUG/SES_ENABLED/ALLOWED_HOSTS). This:
#   * moves the two RDS-bound costs (check ~28s + migrate ~8.8s) off every
#     serving container's pre-bind path;
#   * makes the pre-deploy task the SINGLE migrator (#336);
#   * STRENGTHENS the #529 misconfig gate — a bad config (e.g. email_app.E001)
#     now fails the DEPLOY before any container serves, instead of promoting a
#     crash-looping container.
#
# FAIL-CLOSED: if migrate or check exits non-zero, this function calls
# `exit 1`, which aborts the whole deploy. The service rollout is never
# reached, so the service stays on its OLD task def (not rolled).
run_predeploy_migrate_check() {
    local SERVICE=$1
    local TASK_DEF_ARN=$2

    echo ""
    echo "=== Pre-deploy migrate + check (BOOT_MODE=predeploy) on ${TASK_DEF_ARN} ==="

    # Reuse the service's real network configuration so the one-off task lands
    # in the same subnets/security groups and can reach RDS.
    local NETWORK_CONFIG
    NETWORK_CONFIG=$(aws ecs describe-services \
        --cluster ${CLUSTER} \
        --services ${SERVICE} \
        --query 'services[0].networkConfiguration' \
        --output json)

    if [ -z "${NETWORK_CONFIG}" ] || [ "${NETWORK_CONFIG}" = "null" ]; then
        echo "Error: Could not read networkConfiguration for ${SERVICE}; cannot run pre-deploy task."
        exit 1
    fi

    # Launch config: prefer the service's capacityProviderStrategy; fall back
    # to its launchType (Fargate). run-task requires one or the other.
    local CAP_PROVIDER
    CAP_PROVIDER=$(aws ecs describe-services \
        --cluster ${CLUSTER} \
        --services ${SERVICE} \
        --query 'services[0].capacityProviderStrategy' \
        --output json)
    local LAUNCH_TYPE
    LAUNCH_TYPE=$(aws ecs describe-services \
        --cluster ${CLUSTER} \
        --services ${SERVICE} \
        --query 'services[0].launchType' \
        --output text)

    local LAUNCH_ARGS
    if [ -n "${CAP_PROVIDER}" ] && [ "${CAP_PROVIDER}" != "null" ] && [ "${CAP_PROVIDER}" != "[]" ]; then
        LAUNCH_ARGS=(--capacity-provider-strategy "${CAP_PROVIDER}")
    else
        LAUNCH_ARGS=(--launch-type "${LAUNCH_TYPE}")
    fi

    # The pre-deploy override runs the ESSENTIAL (web) container in predeploy
    # mode (migrate + check + exit 0). The essential container is the single
    # migrator; when it exits, ECS stops the whole task, so a combined-task
    # worker sidecar does NOT linger as a qcluster.
    local ESSENTIAL_NAME
    # shellcheck disable=SC2016  # backticks are a JMESPath boolean literal, not a shell command substitution
    ESSENTIAL_NAME=$(aws ecs describe-task-definition \
        --task-definition ${TASK_DEF_ARN} \
        --query 'taskDefinition.containerDefinitions[?essential==`true`].name | [0]' \
        --output text)

    if [ -z "${ESSENTIAL_NAME}" ] || [ "${ESSENTIAL_NAME}" = "None" ]; then
        echo "Error: Could not determine the essential container for ${TASK_DEF_ARN}."
        exit 1
    fi

    local OVERRIDES
    OVERRIDES=$(printf '{"containerOverrides":[{"name":"%s","environment":[{"name":"BOOT_MODE","value":"predeploy"}]}]}' "${ESSENTIAL_NAME}")

    echo "Running one-off migrate+check task on container ${ESSENTIAL_NAME}..."
    local TASK_ARN
    TASK_ARN=$(aws ecs run-task \
        --cluster ${CLUSTER} \
        --task-definition ${TASK_DEF_ARN} \
        "${LAUNCH_ARGS[@]}" \
        --network-configuration "${NETWORK_CONFIG}" \
        --overrides "${OVERRIDES}" \
        --started-by "predeploy-${TAG}" \
        --query 'tasks[0].taskArn' \
        --output text)

    if [ -z "${TASK_ARN}" ] || [ "${TASK_ARN}" = "None" ]; then
        echo "Error: Pre-deploy run-task did not return a task ARN. Aborting deploy; service NOT rolled."
        exit 1
    fi

    echo "Waiting for pre-deploy task ${TASK_ARN} to reach STOPPED (timeout ~10 min)..."
    if ! aws ecs wait tasks-stopped \
        --cluster ${CLUSTER} \
        --tasks ${TASK_ARN}; then
        echo "ERROR: pre-deploy task did not reach STOPPED. Aborting deploy; service NOT rolled."
        aws ecs describe-tasks \
            --cluster ${CLUSTER} \
            --tasks ${TASK_ARN} \
            --query 'tasks[].{last:lastStatus,stoppedReason:stoppedReason,containers:containers[].{name:name,exitCode:exitCode,reason:reason}}' \
            --output json || true
        exit 1
    fi

    # Read the ESSENTIAL container's exit code — that is the migrate+check
    # result. Any other container (e.g. a killed sidecar) is ignored.
    local EXIT_CODE
    EXIT_CODE=$(aws ecs describe-tasks \
        --cluster ${CLUSTER} \
        --tasks ${TASK_ARN} \
        --query "tasks[0].containers[?name=='${ESSENTIAL_NAME}'].exitCode | [0]" \
        --output text)

    if [ "${EXIT_CODE}" != "0" ]; then
        echo "ERROR: pre-deploy migrate+check exited with code '${EXIT_CODE}' (expected 0)."
        echo "A failing migrate or #529 check FAILS the deploy: service NOT rolled (stays on its old task def)."
        aws ecs describe-tasks \
            --cluster ${CLUSTER} \
            --tasks ${TASK_ARN} \
            --query 'tasks[].{last:lastStatus,stoppedReason:stoppedReason,containers:containers[].{name:name,exitCode:exitCode,reason:reason}}' \
            --output json || true
        exit 1
    fi

    echo "Pre-deploy migrate+check succeeded (exit 0). Proceeding to roll ${SERVICE}."
}

# Roll SERVICE to NEW_TASK_DEF_ARN and wait for steady state. Assumes the
# pre-deploy migrate+check gate already passed.
roll_service() {
    local SERVICE=$1
    local ROLE=$2
    local NEW_TASK_DEF_ARN=$3

    echo ""
    echo "=== Rolling ${SERVICE} (role=${ROLE}) to ${NEW_TASK_DEF_ARN} ==="

    echo "Updating ECS service..."
    # Dev may be provisioned at desired_count=0 to reduce idle Fargate cost.
    # Worker services also run from a 0 baseline so stale tasks do not linger.
    # Each rollout that targets either path must wake the service with the new
    # task definition so ECS has a task to register with the ALB.
    if [ "${ENV}" = "dev" ] || [ "${ROLE}" = "worker" ]; then
        aws ecs update-service \
            --cluster ${CLUSTER} \
            --service ${SERVICE} \
            --task-definition ${NEW_TASK_DEF_ARN} \
            --desired-count 1 \
            > /dev/null
    else
        aws ecs update-service \
            --cluster ${CLUSTER} \
            --service ${SERVICE} \
            --task-definition ${NEW_TASK_DEF_ARN} \
            > /dev/null
    fi

    echo "Waiting for ${SERVICE} to reach steady state on ${NEW_TASK_DEF_ARN} (timeout ~10 min)..."
    if ! aws ecs wait services-stable \
        --cluster ${CLUSTER} \
        --services ${SERVICE}; then
        echo "ERROR: ${SERVICE} did not reach steady state."

        # /ping fallback only makes sense for the web service. ECS can still
        # finish target registration just after the waiter hits its fixed
        # ceiling, so allow a short grace window before declaring failure.
        if [ "${ROLE}" != "worker" ] && [ -n "${DEPLOY_HOST}" ]; then
            local DEPLOYED_TAG
            local GRACE_ATTEMPTS=30
            local GRACE_SLEEP_SECONDS=10

            echo "ECS waiter timed out; polling ${DEPLOY_HOST}/ping for ${TAG} for up to $((GRACE_ATTEMPTS * GRACE_SLEEP_SECONDS))s..."
            for i in $(seq 1 ${GRACE_ATTEMPTS}); do
                DEPLOYED_TAG=$(curl -fsSL --max-time 10 "${DEPLOY_HOST}/ping" 2>/dev/null || true)
                if [ "${DEPLOYED_TAG}" = "${TAG}" ]; then
                    echo "WARNING: ECS waiter timed out, but ${DEPLOY_HOST}/ping is serving ${DEPLOYED_TAG} after grace attempt ${i}."
                    echo "Treating deployment as successful so post-deploy bookkeeping can continue."
                    return 0
                fi
                echo "Grace attempt ${i}/${GRACE_ATTEMPTS}: ${DEPLOY_HOST}/ping returned '${DEPLOYED_TAG:-<unreachable>}'; expected '${TAG}'."
                sleep ${GRACE_SLEEP_SECONDS}
            done
            echo "${DEPLOY_HOST}/ping returned '${DEPLOYED_TAG:-<unreachable>}'; expected '${TAG}'."
        fi

        diagnose_service_failure "${SERVICE}" "${ROLE}"
        exit 1
    fi
}

# Register -> pre-deploy migrate+check gate -> roll, for a single service.
# The gate runs BEFORE the rollout, so a failing migrate/check aborts the
# deploy and the service is never rolled.
deploy_service() {
    local SERVICE=$1
    local ROLE=$2

    register_task_def "${SERVICE}" "${ROLE}"
    local NEW_TASK_DEF_ARN="${REGISTERED_TASK_DEF_ARN}"

    if predeploy_migrate_check_enabled; then
        run_predeploy_migrate_check "${SERVICE}" "${NEW_TASK_DEF_ARN}"
    else
        echo ""
        echo "=== Pre-deploy migrate + check gate disabled ==="
        echo "PREDEPLOY_MIGRATE_CHECK_ENABLED is not true; using legacy RUN_MIGRATIONS serving-container path."
    fi

    roll_service "${SERVICE}" "${ROLE}" "${NEW_TASK_DEF_ARN}"
}

# Prod runs web and worker as separate ECS services for memory isolation.
# The pre-deploy migrate+check task must run EXACTLY ONCE (against the web
# task-def) and complete exit 0 BEFORE either service is rolled — this keeps
# the single-migrator invariant (#336) and migrate-before-serve for both
# services. Worker is rolled first so its replacement is already running
# before the web rollout drops the old sidecar.
if [ "${ENV}" = "prod" ] && predeploy_migrate_check_enabled; then
    WEB_SERVICE="ai-shipping-labs-${ENV}"
    WORKER_SERVICE="ai-shipping-labs-worker-${ENV}"

    register_task_def "${WORKER_SERVICE}" "worker"
    WORKER_TASK_DEF_ARN="${REGISTERED_TASK_DEF_ARN}"

    register_task_def "${WEB_SERVICE}" "web"
    WEB_TASK_DEF_ARN="${REGISTERED_TASK_DEF_ARN}"

    # Single pre-deploy migrate+check, using the web task-def, before EITHER
    # service is rolled. Aborts the whole deploy (nothing rolled) on failure.
    run_predeploy_migrate_check "${WEB_SERVICE}" "${WEB_TASK_DEF_ARN}"

    roll_service "${WORKER_SERVICE}" "worker" "${WORKER_TASK_DEF_ARN}"
    roll_service "${WEB_SERVICE}" "web" "${WEB_TASK_DEF_ARN}"
elif [ "${ENV}" = "prod" ]; then
    deploy_service "ai-shipping-labs-worker-${ENV}" "worker"
    deploy_service "ai-shipping-labs-${ENV}" "web"
else
    deploy_service "ai-shipping-labs-${ENV}" "combined"
fi

echo ""
echo "${ENV} deployment completed successfully."
