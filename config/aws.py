import boto3

from config.settings import settings


def get_boto3_client(service_name: str):
    """The single place the RDS MCP server gets any boto3 client from.

    Explicit credentials from Settings (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in
    .env) take priority when set -- required inside Docker, where there's no host
    ~/.aws to fall back on. Left blank, boto3 falls back to its own default
    credential chain (a configured AWS CLI profile, instance role, etc.).
    """
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        return boto3.client(
            service_name,
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
    return boto3.client(service_name, region_name=settings.aws_region)
