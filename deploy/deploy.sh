#!/bin/bash

set -e

cd "$(dirname "$0")/.."

ENV=${1:-dev}
AWS_REGION="eu-west-1"
REPO_URI="387546586013.dkr.ecr.eu-west-1.amazonaws.com/ai-shipping-labs"
TAG=$(date +'%Y%m%d-%H%M%S')-$(git rev-parse --short HEAD)

echo "Deploying tag ${TAG} to ${ENV}"

echo "Logging in to ECR..."
aws ecr get-login-password --region ${AWS_REGION} \
  | docker login --username AWS --password-stdin ${REPO_URI%%/*}

echo "Building Docker image..."
docker build -t ai-shipping-labs:${TAG} .

echo "Pushing to ECR..."
docker tag ai-shipping-labs:${TAG} ${REPO_URI}:${TAG}
docker push ${REPO_URI}:${TAG}

echo "Deploying to ${ENV}..."
bash deploy/deploy_dev.sh ${TAG} ${ENV}

echo ""
echo "Deployed ${TAG} to ${ENV}"
