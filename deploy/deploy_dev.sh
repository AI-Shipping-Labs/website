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
TASK_DEF="ai-shipping-labs-${ENV}"
SERVICE="ai-shipping-labs-${ENV}"
DEPLOY_HOST=""

if [ "${ENV}" = "dev" ]; then
    DEPLOY_HOST="https://dev.aishippinglabs.com"
elif [ "${ENV}" = "prod" ]; then
    DEPLOY_HOST="https://aishippinglabs.com"
fi

echo "Deploying ${TASK_DEF} with tag ${TAG} to ${ENV} environment"

FILE_IN="${TASK_DEF}-${TAG}.json"
FILE_OUT="updated_${TASK_DEF}-${TAG}.json"

echo "Fetching task definition currently used by ${SERVICE}..."
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
python update_task_def.py ${FILE_IN} ${TAG} ${FILE_OUT} ${ENV}

echo "Registering new task definition..."
NEW_TASK_DEF_ARN=$(aws ecs register-task-definition \
    --cli-input-json file://${FILE_OUT} \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text)

if [ -z "${NEW_TASK_DEF_ARN}" ] || [ "${NEW_TASK_DEF_ARN}" = "None" ]; then
    echo "Error: Task definition registration did not return an ARN."
    exit 1
fi

echo "Updating ECS service..."
aws ecs update-service \
    --cluster ${CLUSTER} \
    --service ${SERVICE} \
    --task-definition ${NEW_TASK_DEF_ARN} \
    > /dev/null

echo "Waiting for ${SERVICE} to reach steady state on ${NEW_TASK_DEF_ARN} (timeout ~10 min)..."
if ! aws ecs wait services-stable \
    --cluster ${CLUSTER} \
    --services ${SERVICE}; then
    echo "ERROR: ${SERVICE} did not reach steady state."

    echo "--- Recent ECS service events ---"
    aws ecs describe-services \
        --cluster ${CLUSTER} \
        --services ${SERVICE} \
        --query 'services[0].events[:20]' \
        --output table || true

    echo "--- RUNNING task statuses ---"
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

    echo "--- ALB target-group health ---"
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

    if [ -n "${DEPLOY_HOST}" ]; then
        echo "--- Live /ping verification ---"
        DEPLOYED_TAG=$(curl -fsSL --max-time 10 "${DEPLOY_HOST}/ping" 2>/dev/null || true)
        if [ "${DEPLOYED_TAG}" = "${TAG}" ]; then
            echo "WARNING: ECS waiter timed out, but ${DEPLOY_HOST}/ping is serving ${DEPLOYED_TAG}."
            echo "Treating deployment as successful so post-deploy bookkeeping can continue."
            rm -f ${FILE_IN} ${FILE_OUT}
            exit 0
        fi
        echo "${DEPLOY_HOST}/ping returned '${DEPLOYED_TAG:-<unreachable>}'; expected '${TAG}'."
    fi

    exit 1
fi

rm -f ${FILE_IN} ${FILE_OUT}

echo "${ENV} deployment completed successfully."
