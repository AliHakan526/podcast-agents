import json
import os
import re
import boto3
from datetime import datetime, timezone
from botocore.config import Config

# Initialize AWS SDK clients
dynamodb = boto3.resource('dynamodb')
agentcore_runtime = boto3.client(
    'bedrock-agentcore',
    config=Config(
        connect_timeout=10,
        read_timeout=840,
        retries={"max_attempts": 1},
    ),
)

# Environment Variables
TABLE_NAME = os.environ.get("JOBS_TABLE_NAME", "PodcastJobsTable")
SUPERVISOR_AGENT_RUNTIME_ARN = os.environ.get("SUPERVISOR_AGENT_RUNTIME_ARN")
SUPERVISOR_AGENT_RUNTIME_QUALIFIER = os.environ.get("SUPERVISOR_AGENT_RUNTIME_QUALIFIER", "DEFAULT")

table = dynamodb.Table(TABLE_NAME)

def extract_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if hasattr(value, "read"):
        return extract_text(value.read())
    if isinstance(value, list):
        return "\n".join(part for part in (extract_text(item) for item in value) if part)
    if isinstance(value, dict):
        text_values = []
        for key in ("text", "content", "result", "message", "artifact", "artifacts", "parts"):
            if key in value:
                text = extract_text(value[key])
                if text:
                    text_values.append(text)
        if text_values:
            return "\n".join(text_values)
        return json.dumps(value)
    return str(value)

def extract_audio_url(text):
    if not text:
        return None
    match = re.search(r"https://[^\s]+", text)
    return match.group(0) if match else None

def lambda_handler(event, context):
    job_id = event.get('job_id')
    message_text = (event.get('message') or '').strip()
    conversation_id = event.get('conversation_id') or job_id
    topic = event.get('topic')
    use_case = event.get('use_case', 'podcast')
    personas = event.get('personas', [])

    if not message_text and topic:
        persona_text = f" with personas: {', '.join(personas)}" if personas else ""
        message_text = f"Create a {use_case or 'podcast'} about {topic}{persona_text}."

    if not job_id or not message_text:
        print("Error: Missing job_id or message in worker payload.")
        return

    try:
        if not SUPERVISOR_AGENT_RUNTIME_ARN:
            raise ValueError(
                "Missing SUPERVISOR_AGENT_RUNTIME_ARN environment variable."
            )

        message = {
            "jsonrpc": "2.0",
            "id": job_id,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": message_text}],
                    "messageId": job_id,
                }
            },
        }

        # 2. Invoke AgentCore Runtime. runtimeSessionId is stable per conversation.
        response = agentcore_runtime.invoke_agent_runtime(
            agentRuntimeArn=SUPERVISOR_AGENT_RUNTIME_ARN,
            runtimeSessionId=conversation_id,
            qualifier=SUPERVISOR_AGENT_RUNTIME_QUALIFIER,
            payload=json.dumps(message).encode("utf-8"),
        )

        # 3. Consume AgentCore Runtime response.
        response_body = response.get("response")
        if hasattr(response_body, "read"):
            full_response_text = response_body.read().decode("utf-8")
        else:
            chunks = []
            for chunk in response_body or []:
                if isinstance(chunk, bytes):
                    chunks.append(chunk.decode("utf-8"))
                else:
                    chunks.append(str(chunk))
            full_response_text = "".join(chunks)

        # 4. Attempt to parse JSON if returned by agent, else save string directly
        try:
            agent_response = json.loads(full_response_text)
        except Exception:
            parsed_result = {"response": full_response_text}
        else:
            if "error" in agent_response:
                raise RuntimeError(json.dumps(agent_response["error"]))

            task = agent_response.get("result", {})
            task_status = task.get("status", {}) if isinstance(task, dict) else {}
            task_state = task_status.get("state")
            if task_state == "failed":
                failure_text = extract_text(task_status.get("message")) or "Agent execution failed"
                raise RuntimeError(failure_text)

            output = extract_text(agent_response.get("result", agent_response))
            parsed_result = {"response": output} if output else {"response": ""}

        audio_url = extract_audio_url(parsed_result.get("response"))
        if audio_url:
            parsed_result["audio_url"] = audio_url

        # 5. Update DynamoDB with COMPLETED status and final output payload
        table.update_item(
            Key={'job_id': job_id},
            UpdateExpression="SET #s = :status, #r = :result, response_text = :response, audio_url = :audio_url, updated_at = :time",
            ExpressionAttributeNames={
                '#s': 'status',
                '#r': 'result'
            },
            ExpressionAttributeValues={
                ':status': 'COMPLETED',
                ':result': parsed_result,
                ':response': parsed_result.get("response", ""),
                ':audio_url': parsed_result.get("audio_url", ""),
                ':time': datetime.now(timezone.utc).isoformat()
            }
        )

    except Exception as e:
        print(f"Error executing job {job_id}: {str(e)}")
        # 6. Mark job as FAILED in DynamoDB on error
        table.update_item(
            Key={'job_id': job_id},
            UpdateExpression="SET #s = :status, error_message = :err, updated_at = :time",
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={
                ':status': 'FAILED',
                ':err': str(e),
                ':time': datetime.now(timezone.utc).isoformat()
            }
        )
