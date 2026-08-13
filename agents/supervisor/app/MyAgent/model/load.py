from strands.models.bedrock import BedrockModel


def load_model() -> BedrockModel:
    """Get Bedrock model client using IAM credentials."""
    return BedrockModel(
        model_id="eu.amazon.nova-2-lite-v1:0",
        max_tokens=4096,
        temperature=0.4,
    )
