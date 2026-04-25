#!/bin/bash

set -e

cd "$(dirname "$0")"

DEV_TAG=$1

if [ -z "$DEV_TAG" ]; then
    echo "No tag provided. Fetching tag from the dev environment."
    TASK_DEF="ai-shipping-labs-dev"

    FILE_IN="${TASK_DEF}-current.json"

    aws ecs describe-task-definition \
        --task-definition ${TASK_DEF} \
        > ${FILE_IN}

    # Multiple containers (web + qcluster) carry the same VERSION env, so jq
    # returns one line per container. Collapse to a single value before
    # passing to deploy_dev.sh — otherwise word-splitting eats the env arg.
    DEV_TAG=$(
        jq '.taskDefinition.containerDefinitions[].environment[] | select(.name == "VERSION").value' -r ${FILE_IN} | sort -u | head -1
    )

    rm -f ${FILE_IN}
fi

echo "Deploying ${DEV_TAG} to prod"

if [ -z "${GITHUB_ACTIONS}" ] && [ "${CONFIRM_DEPLOY}" != "true" ]; then
    read -p "Are you sure you want to deploy to production? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        CONFIRM_DEPLOY="true"
    fi
fi

if [ "${CONFIRM_DEPLOY}" != "true" ]; then
    echo "Exiting without deploying."
    exit 1
fi

bash deploy_dev.sh ${DEV_TAG} prod

echo "${DEV_TAG}" >> ../.prod-versions
