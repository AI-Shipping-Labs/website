#!/bin/bash

set -e

cd "$(dirname "$0")"

DEV_TAG=$1

if [ -z "$DEV_TAG" ]; then
    echo "No tag provided. Fetching tag from dev /ping endpoint."
    DEV_TAG=$(curl -fsSL --max-time 10 https://dev.aishippinglabs.com/ping)
    if [ -z "$DEV_TAG" ]; then
        echo "ERROR: failed to read dev /ping for auto-detect."
        exit 1
    fi
    echo "Auto-detected dev tag: ${DEV_TAG}"
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
