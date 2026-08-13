import json
import os
import boto3
from decimal import Decimal

# Initialize DynamoDB client
dynamodb = boto3.resource('dynamodb')

# Environment variable for the DynamoDB table
TABLE_NAME = os.environ.get("JOBS_TABLE_NAME", "PodcastJobsTable")
table = dynamodb.Table(TABLE_NAME)

def json_default(value):
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

def lambda_handler(event, context):
    try:
        # 1. Extract job_id from query parameters (e.g., GET /status?job_id=1234)
        query_params = event.get('queryStringParameters') or {}
        job_id = query_params.get('job_id')

        if not job_id:
            return {
                "statusCode": 400,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*"
                },
                "body": json.dumps({"error": "Missing required query parameter 'job_id'"})
            }

        # 2. Retrieve the job status record from DynamoDB
        response = table.get_item(Key={'job_id': job_id})
        item = response.get('Item')

        if not item:
            return {
                "statusCode": 404,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*"
                },
                "body": json.dumps({"error": f"Job with ID '{job_id}' not found."})
            }

        # 3. Format and return the payload to the client
        result = item.get('result') or {}
        response_text = item.get('response_text')
        audio_url = item.get('audio_url')

        if not response_text and isinstance(result, dict):
            response_text = result.get('response') or result.get('output')
            audio_url = audio_url or result.get('audio_url')
        elif not response_text and isinstance(result, str):
            response_text = result

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "job_id": item.get('job_id'),
                "conversation_id": item.get('conversation_id'),
                "status": item.get('status'),         # 'PROCESSING', 'COMPLETED', or 'FAILED'
                "use_case": item.get('use_case'),
                "response": response_text or "",
                "audio_url": audio_url or "",
                "error": item.get('error_message'),   # Population only if status == 'FAILED'
                "updated_at": item.get('updated_at')
            }, default=json_default)
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({"error": str(e)})
        }
