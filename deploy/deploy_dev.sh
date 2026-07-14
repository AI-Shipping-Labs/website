#!/bin/bash

set -e

cd "$(dirname "$0")"

# Shared monotonic-deadline polling used by this script and wake-dev-ecs.
# shellcheck source=deploy/readiness_poll.sh
source "./readiness_poll.sh"

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

# Issue #1140 stopgap for the cold-start ELB health race observed on
# 2026-07-08: dev took ~21 minutes to converge after crash-looping slow
# cold-start tasks past the ALB health budget. The real cure is
# DataTalksClub/aws-infra#11 (ECS health_check_grace_period_seconds and
# target-group tuning). Until then, the deploy runner waits longer for the
# exact VERSION tag to appear at /ping after the ECS services-stable waiter
# times out.
#
# Defaults: a HARD 1500-second monotonic post-waiter deadline, polling every
# 10 seconds, capped at 150 attempts, with 3 consecutive exact VERSION
# responses required. curl and sleep consume the SAME deadline; a slow curl
# cannot double the documented ceiling. Override the DEPLOY_GRACE_* values in
# the shell/CI env to adjust the bounded window without editing code. These are
# deliberately deploy-time shell variables, not IntegrationSettings: the CI
# runner must read them before the Django app is reachable, including the
# failure mode where the app never boots.
DEFAULT_DEPLOY_GRACE_TIMEOUT_SECONDS=1500
DEFAULT_DEPLOY_GRACE_POLL_SECONDS=10
DEFAULT_DEPLOY_GRACE_ATTEMPTS=150
DEFAULT_DEPLOY_GRACE_REQUIRED_MATCHES=3

configured_deploy_grace_timeout_seconds() {
    local VALUE="${DEPLOY_GRACE_TIMEOUT_SECONDS:-${DEFAULT_DEPLOY_GRACE_TIMEOUT_SECONDS}}"
    if ! readiness_is_positive_integer "${VALUE}"; then
        echo "ERROR: DEPLOY_GRACE_TIMEOUT_SECONDS must be a positive integer; got '${VALUE}'." >&2
        exit 1
    fi
    echo "${VALUE}"
}

configured_deploy_grace_poll_seconds() {
    # DEPLOY_GRACE_SLEEP_SECONDS remains a one-release compatibility alias.
    local VALUE="${DEPLOY_GRACE_POLL_SECONDS:-${DEPLOY_GRACE_SLEEP_SECONDS:-${DEFAULT_DEPLOY_GRACE_POLL_SECONDS}}}"
    if ! readiness_is_non_negative_integer "${VALUE}"; then
        echo "ERROR: DEPLOY_GRACE_POLL_SECONDS must be a non-negative integer; got '${VALUE}'." >&2
        exit 1
    fi
    echo "${VALUE}"
}

configured_deploy_grace_attempts() {
    local VALUE="${DEPLOY_GRACE_MAX_ATTEMPTS:-${DEPLOY_GRACE_ATTEMPTS:-${DEFAULT_DEPLOY_GRACE_ATTEMPTS}}}"
    if ! readiness_is_positive_integer "${VALUE}"; then
        echo "ERROR: DEPLOY_GRACE_ATTEMPTS must be a positive integer; got '${VALUE}'." >&2
        exit 1
    fi
    echo "${VALUE}"
}

configured_deploy_grace_required_matches() {
    local VALUE="${DEPLOY_GRACE_REQUIRED_MATCHES:-${DEFAULT_DEPLOY_GRACE_REQUIRED_MATCHES}}"
    if ! readiness_is_positive_integer "${VALUE}"; then
        echo "ERROR: DEPLOY_GRACE_REQUIRED_MATCHES must be a positive integer; got '${VALUE}'." >&2
        exit 1
    fi
    echo "${VALUE}"
}

validate_deploy_grace_configuration() {
    configured_deploy_grace_timeout_seconds > /dev/null
    configured_deploy_grace_poll_seconds > /dev/null
    configured_deploy_grace_attempts > /dev/null
    configured_deploy_grace_required_matches > /dev/null
}

# Reject invalid deploy controls before registering a task definition or
# updating a service, even when the normal ECS waiter would have succeeded.
validate_deploy_grace_configuration

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

RECOVERY_SERVICE=""
RECOVERY_TASK_DEF_ARN=""
RECOVERY_ECS_SUMMARY="not checked"

recovery_aws_before_deadline() {
    local DEADLINE=$1
    shift
    local NOW
    local REMAINING

    NOW=$(readiness_monotonic_seconds) || return 1
    REMAINING=$((DEADLINE - NOW))
    if [ "${REMAINING}" -le 0 ]; then
        return 1
    fi

    # ``timeout`` bounds AWS network retries inside the same recovery
    # deadline. Recompute remaining time before every read-only call so a
    # slow ECS response cannot extend a nominal 1500-second window.
    timeout --signal=TERM "${REMAINING}s" aws "$@"
}

verify_recovery_ecs_state() {
    local DEADLINE=$1
    local PRIMARY_STATE
    local PRIMARY_TASK_DEF
    local DESIRED_COUNT
    local RUNNING_COUNT
    local EXPECTED_CONTAINER_NAMES
    local RUNNING_ARNS
    local TASK_ARN
    local TASK_STATE
    local TASK_DEF
    local TASK_LAST_STATUS
    local CONTAINER_STATES
    local EXPECTED_NAME
    local MATCHING_TASK=""

    PRIMARY_STATE=$(recovery_aws_before_deadline "${DEADLINE}" ecs describe-services \
        --cluster "${CLUSTER}" \
        --services "${RECOVERY_SERVICE}" \
        --query "services[0].deployments[?status=='PRIMARY'] | [0].[taskDefinition,desiredCount,runningCount]" \
        --output text 2>/dev/null || true)
    read -r PRIMARY_TASK_DEF DESIRED_COUNT RUNNING_COUNT <<< "${PRIMARY_STATE}"

    RECOVERY_ECS_SUMMARY="primary=${PRIMARY_TASK_DEF:-<missing>} desired=${DESIRED_COUNT:-<missing>} running=${RUNNING_COUNT:-<missing>} containers=<unchecked>"
    if [ "${PRIMARY_TASK_DEF}" != "${RECOVERY_TASK_DEF_ARN}" ] || \
            ! readiness_is_non_negative_integer "${DESIRED_COUNT:-}" || \
            ! readiness_is_non_negative_integer "${RUNNING_COUNT:-}" || \
            [ "${RUNNING_COUNT}" -lt "${DESIRED_COUNT}" ]; then
        echo "ECS recovery state not ready: ${RECOVERY_ECS_SUMMARY}"
        return 1
    fi

    EXPECTED_CONTAINER_NAMES=$(recovery_aws_before_deadline "${DEADLINE}" ecs describe-task-definition \
        --task-definition "${RECOVERY_TASK_DEF_ARN}" \
        --query 'taskDefinition.containerDefinitions[].name' \
        --output text 2>/dev/null || true)
    if [ -z "${EXPECTED_CONTAINER_NAMES}" ] || [ "${EXPECTED_CONTAINER_NAMES}" = "None" ]; then
        RECOVERY_ECS_SUMMARY="${RECOVERY_ECS_SUMMARY% containers=*} containers=<task-definition-unavailable>"
        echo "ECS recovery state not ready: ${RECOVERY_ECS_SUMMARY}"
        return 1
    fi

    RUNNING_ARNS=$(recovery_aws_before_deadline "${DEADLINE}" ecs list-tasks \
        --cluster "${CLUSTER}" \
        --service-name "${RECOVERY_SERVICE}" \
        --desired-status RUNNING \
        --query 'taskArns' \
        --output text 2>/dev/null || true)
    for TASK_ARN in ${RUNNING_ARNS}; do
        TASK_STATE=$(recovery_aws_before_deadline "${DEADLINE}" ecs describe-tasks \
            --cluster "${CLUSTER}" \
            --tasks "${TASK_ARN}" \
            --query 'tasks[0].[taskDefinitionArn,lastStatus]' \
            --output text 2>/dev/null || true)
        read -r TASK_DEF TASK_LAST_STATUS <<< "${TASK_STATE}"
        echo "ECS recovery candidate: task=${TASK_ARN} task_definition=${TASK_DEF:-<missing>} last_status=${TASK_LAST_STATUS:-<missing>}"
        if [ "${TASK_DEF}" != "${RECOVERY_TASK_DEF_ARN}" ] || \
                [ "${TASK_LAST_STATUS}" != "RUNNING" ]; then
            continue
        fi

        CONTAINER_STATES=$(recovery_aws_before_deadline "${DEADLINE}" ecs describe-tasks \
            --cluster "${CLUSTER}" \
            --tasks "${TASK_ARN}" \
            --query 'tasks[0].containers[].[name,lastStatus]' \
            --output text 2>/dev/null || true)
        MATCHING_TASK=${TASK_ARN}
        for EXPECTED_NAME in ${EXPECTED_CONTAINER_NAMES}; do
            if ! printf '%s\n' "${CONTAINER_STATES}" | awk -v name="${EXPECTED_NAME}" \
                    '$1 == name && $2 == "RUNNING" { found=1 } END { exit(found ? 0 : 1) }'; then
                MATCHING_TASK=""
                break
            fi
        done
        if [ -n "${MATCHING_TASK}" ]; then
            RECOVERY_ECS_SUMMARY="primary=${PRIMARY_TASK_DEF} desired=${DESIRED_COUNT} running=${RUNNING_COUNT} task=${MATCHING_TASK} task_last_status=${TASK_LAST_STATUS} containers=$(printf '%s' "${CONTAINER_STATES}" | tr '\n' ',' | sed 's/,$//')"
            echo "ECS recovery state ready: ${RECOVERY_ECS_SUMMARY}"
            return 0
        fi
    done

    RECOVERY_ECS_SUMMARY="${RECOVERY_ECS_SUMMARY% containers=*} containers=<no-matching-all-running-task>"
    echo "ECS recovery state not ready: ${RECOVERY_ECS_SUMMARY}"
    return 1
}

wait_for_deployed_tag_after_waiter_timeout() {
    local SERVICE=$1
    local NEW_TASK_DEF_ARN=$2
    local GRACE_TIMEOUT_SECONDS
    local GRACE_POLL_SECONDS
    local GRACE_ATTEMPTS
    local GRACE_REQUIRED_MATCHES

    GRACE_TIMEOUT_SECONDS=$(configured_deploy_grace_timeout_seconds)
    GRACE_POLL_SECONDS=$(configured_deploy_grace_poll_seconds)
    GRACE_ATTEMPTS=$(configured_deploy_grace_attempts)
    GRACE_REQUIRED_MATCHES=$(configured_deploy_grace_required_matches)

    RECOVERY_SERVICE=${SERVICE}
    RECOVERY_TASK_DEF_ARN=${NEW_TASK_DEF_ARN}
    RECOVERY_ECS_SUMMARY="not checked"

    echo "ECS waiter timed out; applying a hard ${GRACE_TIMEOUT_SECONDS}s monotonic recovery deadline for ${DEPLOY_HOST}/ping tag ${TAG} and task definition ${NEW_TASK_DEF_ARN}."
    if readiness_poll_until_stable \
            "${DEPLOY_HOST}/ping" "${TAG}" \
            "${GRACE_TIMEOUT_SECONDS}" "${GRACE_POLL_SECONDS}" \
            "${GRACE_ATTEMPTS}" "${GRACE_REQUIRED_MATCHES}" \
            verify_recovery_ecs_state; then
        echo "WARNING: ECS waiter timed out, but ${DEPLOY_HOST}/ping served the exact expected tag '${TAG}' stably and ECS confirmed the new revision."
        echo "Recovered deploy evidence: elapsed=${READINESS_ELAPSED_SECONDS}s attempts=${READINESS_ATTEMPTS} consecutive=${READINESS_CONSECUTIVE}/${GRACE_REQUIRED_MATCHES} task_definition=${NEW_TASK_DEF_ARN} ${RECOVERY_ECS_SUMMARY}"
        echo "Treating deployment as successful so post-deploy bookkeeping can continue."
        return 0
    fi

    echo "Recovery deadline exhausted: elapsed=${READINESS_ELAPSED_SECONDS}s attempts=${READINESS_ATTEMPTS} consecutive=${READINESS_CONSECUTIVE}/${GRACE_REQUIRED_MATCHES} response_state=${READINESS_LAST_RESPONSE_STATE} response_bytes=${READINESS_LAST_RESPONSE_BYTES} task_definition=${NEW_TASK_DEF_ARN} ${RECOVERY_ECS_SUMMARY}"
    return 1
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
        # finish target registration after the waiter hits its fixed ceiling,
        # so allow the bounded #1140 grace window before declaring failure.
        if [ "${ROLE}" != "worker" ] && [ -n "${DEPLOY_HOST}" ]; then
            if wait_for_deployed_tag_after_waiter_timeout "${SERVICE}" "${NEW_TASK_DEF_ARN}"; then
                return 0
            fi
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
