import json
import uuid
import os
import boto3
from datetime import datetime, timezone

# Initialize AWS SDK clients
dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

# Environment variables
TABLE_NAME = os.environ.get("JOBS_TABLE_NAME", "PodcastJobsTable")
WORKER_LAMBDA_NAME = os.environ.get("WORKER_LAMBDA_NAME", "bedrock-execution-worker")

table = dynamodb.Table(TABLE_NAME)

def lambda_handler(event, context):
    try:
        # 1. Parse incoming request body from API Gateway
        body = {}
        if event.get('body'):
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']

        message = (body.get('message') or '').strip()
        topic = (body.get('topic') or '').strip()
        use_case = body.get('use_case')
        personas = body.get('personas', [])
        conversation_id = body.get('conversation_id') or str(uuid.uuid4())

        if not message and topic:
            persona_text = f" with personas: {', '.join(personas)}" if personas else ""
            message = f"Create a {use_case or 'podcast'} about {topic}{persona_text}."

        if not message:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
                "body": json.dumps({"error": "Missing required parameter 'message'"})
            }

        # 2. Generate unique job tracking identifier
        job_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        # 3. Create initial state entry in DynamoDB
        table.put_item(
            Item={
                'job_id': job_id,
                'status': 'PROCESSING',
                'conversation_id': conversation_id,
                'message': message,
                'use_case': use_case or 'chat',
                'topic': topic,
                'personas': personas,
                'created_at': created_at,
                'updated_at': created_at
            }
        )

        # 4. Asynchronously invoke the worker Lambda that calls Bedrock Agent runtime
        # InvocationType='Event' makes this a non-blocking asynchronous call
        payload = {
            'job_id': job_id,
            'conversation_id': conversation_id,
            'message': message,
            'use_case': use_case or 'chat',
            'topic': topic,
            'personas': personas
        }

        lambda_client.invoke(
            FunctionName=WORKER_LAMBDA_NAME,
            InvocationType='Event',  # Asynchronous execution trigger
            Payload=json.dumps(payload)
        )

        # 5. Immediately return job_id to the client so the web/mobile UI does not wait
        return {
            "statusCode": 202,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "message": "Job initiated successfully.",
                "job_id": job_id,
                "conversation_id": conversation_id,
                "status": "PROCESSING"
            })
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
