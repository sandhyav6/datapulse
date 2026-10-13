#!/usr/bin/env bash
# Move every message in the DLQ back to the main job queue (SQS native redrive).
# Usage: scripts/redrive_dlq.sh [stack-name]    (uses AWS_PROFILE, default etl-dev)
set -euo pipefail

STACK="${1:-serverless-etl-dev}"
export AWS_PROFILE="${AWS_PROFILE:-etl-dev}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

DLQ_URL="$(output DlqUrl)"
QUEUE_URL="$(output QueueUrl)"
DLQ_ARN="$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)"
QUEUE_ARN="$(aws sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)"
WAITING="$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names ApproximateNumberOfMessages --query Attributes.ApproximateNumberOfMessages --output text)"

echo "DLQ:   $DLQ_ARN ($WAITING message(s))"
echo "Queue: $QUEUE_ARN"
if [ "$WAITING" = "0" ]; then
  echo "Nothing to redrive."
  exit 0
fi

TASK="$(aws sqs start-message-move-task --source-arn "$DLQ_ARN" --destination-arn "$QUEUE_ARN" --query TaskHandle --output text)"
echo "Started move task: $TASK"
aws sqs list-message-move-tasks --source-arn "$DLQ_ARN" --max-results 1 \
  --query "Results[0].{Status:Status,Moved:ApproximateNumberOfMessagesMoved,ToMove:ApproximateNumberOfMessagesToMove}" \
  --output table
echo "Failed jobs are reclaimed from FAILED (attempts goes up by one) and should end SUCCEEDED."
