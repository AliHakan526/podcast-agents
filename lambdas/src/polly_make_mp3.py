import io
import json
import os
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError


polly = boto3.client("polly")
s3 = boto3.client("s3")

VOICE_MAP = {
    "Speaker A": "Joey",
    "Speaker B": "Joanna",
    "Speaker C": "Salli",
}


def turnScriptToAudio(script: list[dict[str, str]] | str) -> dict[str, str]:
    """Convert a speaker script into one MP3 file and return a presigned URL."""
    bucket_name = os.environ.get("PODCAST_AUDIO_BUCKET")
    if not bucket_name:
        raise ValueError("Missing required environment variable PODCAST_AUDIO_BUCKET.")
    polly_engine = os.environ.get("POLLY_ENGINE")

    if isinstance(script, str):
        script = json.loads(script)

    if not isinstance(script, list) or not script:
        raise ValueError("script must be a non-empty JSON array")

    final_audio = io.BytesIO()

    for line in script:
        if not isinstance(line, dict):
            raise ValueError("each script item must be an object")

        speaker = line.get("speaker", "Speaker A")
        text = line.get("text", "").strip()
        if not text:
            continue

        voice_id = VOICE_MAP.get(speaker, "Joanna")
        synthesize_args = {
            "Text": text,
            "OutputFormat": "mp3",
            "VoiceId": voice_id,
        }
        if polly_engine:
            synthesize_args["Engine"] = polly_engine

        try:
            response = polly.synthesize_speech(**synthesize_args)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            if polly_engine and error.get("Code") == "ValidationException":
                response = polly.synthesize_speech(
                    Text=text,
                    OutputFormat="mp3",
                    VoiceId=voice_id,
                )
            else:
                raise

        if "AudioStream" in response:
            final_audio.write(response["AudioStream"].read())

    if final_audio.tell() == 0:
        raise ValueError("script did not contain any speakable text")

    final_audio.seek(0)
    file_key = f"podcasts/podcast_{uuid.uuid4().hex}.mp3"

    s3.put_object(
        Bucket=bucket_name,
        Key=file_key,
        Body=final_audio.read(),
        ContentType="audio/mpeg",
    )

    presigned_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": file_key},
        ExpiresIn=3600,
    )

    return {
        "audio_url": presigned_url,
        "s3_bucket": bucket_name,
        "s3_key": file_key,
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle AgentCore Gateway Lambda target events."""
    if "script" in event or "script_json" in event:
        try:
            return turnScriptToAudio(event.get("script", event.get("script_json")))
        except Exception as exc:
            return {"error": f"Error generating audio: {exc}"}

    return {"error": "Missing required input: script or script_json"}
