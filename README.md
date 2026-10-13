# Serverless ETL & Data Quality Platform on AWS

An event-driven ETL pipeline: a CSV dropped into S3 is queued in SQS, validated and cleaned by
Lambda against a JSON schema, written back to S3 as clean rows, rejected rows and a quality
report, tracked in DynamoDB, monitored with CloudWatch, and shown on a Streamlit dashboard.
Retries are safe (idempotent), poison files land in a dead-letter queue, and the whole stack
deploys and deletes with one command each (AWS SAM).

## Architecture

## Deploy

## Demo

## Results

## Design decisions

## Cleanup
