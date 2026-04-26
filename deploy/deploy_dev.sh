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

echo "Deploying ${TASK_DEF} with tag ${TAG} to ${ENV} environment"

FILE_IN="${TASK_DEF}-${TAG}.json"
FILE_OUT="updated_${TASK_DEF}-${TAG}.json"

echo "Fetching current task definition..."
aws ecs describe-task-definition \
    --task-definition ${TASK_DEF} \
    > ${FILE_IN}

echo "Updating task definition with new image tag..."
python update_task_def.py ${FILE_IN} ${TAG} ${FILE_OUT} ${ENV}

echo "Registering new task definition..."
aws ecs register-task-definition \
    --cli-input-json file://${FILE_OUT} \
    > /dev/null

echo "Updating ECS service..."
aws ecs update-service \
    --cluster ${CLUSTER} \
    --service ${SERVICE} \
    --task-definition ${TASK_DEF} \
    > /dev/null

echo "Waiting for ${SERVICE} to reach steady state on the new task definition (timeout ~10 min)..."
if ! aws ecs wait services-stable \
    --cluster ${CLUSTER} \
    --services ${SERVICE}; then
    echo "ERROR: ${SERVICE} did not reach steady state. Recent ECS service events:"
    aws ecs describe-services \
        --cluster ${CLUSTER} \
        --services ${SERVICE} \
        --query 'services[0].events[:20]' \
        --output table || true
    echo "ERROR: ${SERVICE} did not reach steady state. Recent task stop reasons:"
    TASK_ARNS=$(aws ecs list-tasks \
        --cluster ${CLUSTER} \
        --service-name ${SERVICE} \
        --desired-status STOPPED \
        --query 'taskArns[:5]' \
        --output text || true)
    if [ -n "${TASK_ARNS}" ]; then
        aws ecs describe-tasks \
            --cluster ${CLUSTER} \
            --tasks ${TASK_ARNS} \
            --query 'tasks[].[taskArn,lastStatus,stoppedReason,containers[].reason]' \
            --output table || true
    fi
    exit 1
fi

rm -f ${FILE_IN} ${FILE_OUT}

echo "${ENV} deployment completed successfully."
