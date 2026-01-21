# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import logging
import os
from typing import Any, Dict, Union

import boto3
import cfnresponse  # type: ignore[import-untyped]
import yaml
from botocore.exceptions import ClientError
from idp_common.config.configuration_manager import (
    ConfigurationManager,  # type: ignore[import-untyped]
)
from pydantic import ValidationError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
logging.getLogger("idp_common.bedrock.client").setLevel(
    os.environ.get("BEDROCK_LOG_LEVEL", "INFO")
)

s3_client = boto3.client("s3")

def fetch_content_from_s3(s3_uri: str) -> Union[Dict[str, Any], str]:
    """
    Fetches content from S3 URI and parses as JSON or YAML if possible
    """
    try:
        # Parse S3 URI
        if not s3_uri.startswith("s3://"):
            raise ValueError(f"Invalid S3 URI: {s3_uri}")

        # Remove s3:// prefix and split bucket and key
        s3_path = s3_uri[5:]
        bucket, key = s3_path.split("/", 1)

        logger.info(f"Fetching content from S3: bucket={bucket}, key={key}")

        # Fetch object from S3
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response["Body"].read().decode("utf-8")

        # Try to parse as JSON first, then YAML, return as string if both fail
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            try:
                return yaml.safe_load(content)
            except yaml.YAMLError:
                logger.warning(
                    f"Content from {s3_uri} is not valid JSON or YAML, returning as string"
                )
                return content

    except ClientError as e:
        logger.error(f"Error fetching content from S3 {s3_uri}: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error processing S3 URI {s3_uri}: {str(e)}")
        raise


def resolve_content(content: Union[str, Dict[str, Any]]) -> Union[Dict[str, Any], str]:
    """
    Resolves content - if it's a string starting with s3://, fetch from S3
    Otherwise return as-is
    """
    if isinstance(content, str) and content.startswith("s3://"):
        return fetch_content_from_s3(content)
    return content

# Model mapping between regions
MODEL_MAPPINGS = {
    "us.amazon.nova-lite-v1:0": "eu.amazon.nova-lite-v1:0",
    "us.amazon.nova-pro-v1:0": "eu.amazon.nova-pro-v1:0",
    "us.amazon.nova-premier-v1:0": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.amazon.nova-2-lite-v1:0": "eu.amazon.nova-2-lite-v1:0",
    "us.anthropic.claude-3-haiku-20240307-v1:0": "eu.anthropic.claude-3-haiku-20240307-v1:0",
    "us.anthropic.claude-3-5-haiku-20241022-v1:0": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0": "eu.anthropic.claude-3-5-sonnet-20241022-v2:0",
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0": "eu.anthropic.claude-3-7-sonnet-20250219-v1:0",
    "us.anthropic.claude-sonnet-4-20250514-v1:0": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
    "us.anthropic.claude-sonnet-4-20250514-v1:0:1m": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0:1m": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0:1m",
    "us.anthropic.claude-opus-4-20250514-v1:0": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-opus-4-1-20250805-v1:0": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-opus-4-5-20251101-v1:0": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
}

def get_current_region() -> str:
    """Get the current AWS region"""
    region = boto3.Session().region_name
    if region is None:
        # Fallback to environment variable or default
        region = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
    return region


def is_eu_region(region: str) -> bool:
    """Check if the region is an EU region"""
    return region.startswith("eu-")


def is_us_region(region: str) -> bool:
    """Check if the region is a US region"""
    return region.startswith("us-")


def get_model_mapping(model_id: str, target_region_type: str) -> str:
    """Get the equivalent model for the target region type"""
    if target_region_type == "eu":
        return MODEL_MAPPINGS.get(model_id, model_id)
    elif target_region_type == "us":
        # Reverse mapping for US
        for us_model, eu_model in MODEL_MAPPINGS.items():
            if model_id == eu_model:
                return us_model
        return model_id
    return model_id


def filter_models_by_region(data: Any, region_type: str) -> Any:
    """Filter out models that don't match the region type"""
    if isinstance(data, dict):
        filtered_data = {}
        for key, value in data.items():
            if isinstance(value, list) and any(
                isinstance(item, str) and ("us." in item or "eu." in item)
                for item in value
            ):
                # This is a model list - filter it
                filtered_list = []
                for item in value:
                    if isinstance(item, str):
                        # Include models that match the region type or are region-agnostic
                        if region_type == "us":
                            # Include US models and non-region-specific models, exclude EU models
                            if item.startswith("us.") or (not item.startswith("eu.") and not item.startswith("us.")):
                                filtered_list.append(item)
                        elif region_type == "eu":
                            # Include EU models and non-region-specific models, exclude US models
                            if item.startswith("eu.") or (not item.startswith("eu.") and not item.startswith("us.")):
                                filtered_list.append(item)
                        else:
                            # For other regions, include all models
                            filtered_list.append(item)
                    else:
                        filtered_list.append(item)
                filtered_data[key] = filtered_list
            else:
                filtered_data[key] = filter_models_by_region(value, region_type)
        return filtered_data
    elif isinstance(data, list):
        return [filter_models_by_region(item, region_type) for item in data]
    return data


def swap_model_ids(data: Any, region_type: str) -> Any:
    """Swap model IDs to match the region type"""
    if isinstance(data, dict):
        swapped_data = {}
        for key, value in data.items():
            if isinstance(value, str) and ("us." in value or "eu." in value):
                # This is a model ID - check if it needs swapping
                if region_type == "us" and value.startswith("eu."):
                    new_model = get_model_mapping(value, "us")
                    if new_model != value:
                        logger.info(f"Swapped EU model {value} to US model {new_model}")
                    swapped_data[key] = new_model
                elif region_type == "eu" and value.startswith("us."):
                    new_model = get_model_mapping(value, "eu")
                    if new_model != value:
                        logger.info(f"Swapped US model {value} to EU model {new_model}")
                    swapped_data[key] = new_model
                else:
                    swapped_data[key] = value
            else:
                swapped_data[key] = swap_model_ids(value, region_type)
        return swapped_data
    elif isinstance(data, list):
        return [swap_model_ids(item, region_type) for item in data]
    return data



def generate_physical_id(stack_id: str, logical_id: str) -> str:
    """
    Generates a consistent physical ID for the custom resource
    """
    return f"{stack_id}/{logical_id}/configuration"


def handler(event: Dict[str, Any], context: Any) -> None:
    """
    Handles CloudFormation Custom Resource events for configuration management
    """
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        request_type = event["RequestType"]
        properties = event["ResourceProperties"]
        stack_id = event["StackId"]
        logical_id = event["LogicalResourceId"]

        # Generate physical ID
        physical_id = generate_physical_id(stack_id, logical_id)

        # Remove ServiceToken from properties as it's not needed in DynamoDB
        properties.pop("ServiceToken", None)

        # Detect region type
        current_region = get_current_region()
        region_type = (
            "eu"
            if is_eu_region(current_region)
            else "us"
            if is_us_region(current_region)
            else "other"
        )
        logger.info(f"Detected region: {current_region}, region type: {region_type}")

        # Initialize ConfigurationManager for all database operations
        manager = ConfigurationManager()

        if request_type in ["Create", "Update"]:
            # Handle migration and versioning
            if request_type == "Create":
                # New stack - create v0 from Default configuration
                logger.info("New stack deployment - will create v0 from Default configuration")
                
            elif request_type == "Update":
                # Existing stack - migrate Default/Custom to v0/v1 versioning
                try:
                    import datetime
                    import hashlib
                    
                    # Check for existing Default and Custom configurations
                    existing_default = manager.get_configuration("Default")
                    existing_custom = manager.get_configuration("Custom")
                    
                    if existing_default:
                        timestamp = datetime.datetime.utcnow().isoformat() + "Z"
                        
                        # Get configuration data for checksum comparison
                        default_data = existing_default.model_dump(mode="python", exclude={"config_type"})
                        
                        if existing_custom:
                            custom_data = existing_custom.model_dump(mode="python", exclude={"config_type"})
                            
                            # Calculate checksums to compare configurations
                            default_checksum = hashlib.md5(json.dumps(default_data, sort_keys=True).encode()).hexdigest()
                            custom_checksum = hashlib.md5(json.dumps(custom_data, sort_keys=True).encode()).hexdigest()
                            
                            if default_checksum == custom_checksum:
                                # Default and Custom are same - create only v0 as active
                                metadata_v0 = {
                                    "is_active": True,
                                    "created_at": timestamp,
                                    "description": "System default configuration (v0)"
                                }
                                manager.save_configuration("v0", default_data, metadata=metadata_v0)
                                logger.info("Default and Custom are identical - created v0 as active")
                                
                                # Clean up old entries
                                manager.delete_configuration("Default")
                                manager.delete_configuration("Custom")
                                logger.info("Deleted old Default and Custom configurations")
                            else:
                                # Default and Custom are different - create both v0 and v1
                                # v0 (Default) - not active
                                metadata_v0 = {
                                    "is_active": False,
                                    "created_at": timestamp,
                                    "description": "System default configuration (v0)"
                                }
                                manager.save_configuration("v0", default_data, metadata=metadata_v0)
                                
                                # v1 (Custom) - active
                                metadata_v1 = {
                                    "is_active": True,
                                    "created_at": timestamp,
                                    "description": "User customized configuration (v1)"
                                }
                                manager.save_configuration("v1", custom_data, metadata=metadata_v1)
                                logger.info("Default and Custom are different - created v0 and v1 (v1 active)")
                                
                                # Clean up old entries
                                manager.delete_configuration("Default")
                                manager.delete_configuration("Custom")
                                logger.info("Deleted old Default and Custom configurations")
                        else:
                            # Only Default exists - create v0 as active
                            metadata_v0 = {
                                "is_active": True,
                                "created_at": timestamp,
                                "description": "System default configuration (v0)"
                            }
                            manager.save_configuration("v0", default_data, metadata=metadata_v0)
                            logger.info("Only Default exists - created v0 as active")
                            
                            # Clean up old Default entry
                            manager.delete_configuration("Default")
                            logger.info("Deleted old Default configuration")
                        
                except Exception as e:
                    logger.warning(f"Failed to migrate existing configurations: {e}")
            
            # Collect all configurations to process
            configurations = {}
            
            # Process Schema configuration
            if "Schema" in properties:
                resolved_schema = resolve_content(properties["Schema"])
                # Filter models based on region
                if region_type in ["us", "eu"]:
                    resolved_schema = filter_models_by_region(resolved_schema, region_type)
                configurations["Schema"] = {"Schema": resolved_schema}

            # Process Default configuration
            if "Default" in properties:
                resolved_default = resolve_content(properties["Default"])
                # Apply custom model ARNs if provided
                if isinstance(resolved_default, dict):
                    # Replace classification model if CustomClassificationModelARN is provided and not empty
                    if (
                        "CustomClassificationModelARN" in properties
                        and properties["CustomClassificationModelARN"].strip()
                    ):
                        if "classification" in resolved_default:
                            resolved_default["classification"]["model"] = properties[
                                "CustomClassificationModelARN"
                            ]
                            logger.info(
                                f"Updated classification model to: {properties['CustomClassificationModelARN']}"
                            )

                    # Replace extraction model if CustomExtractionModelARN is provided and not empty
                    if (
                        "CustomExtractionModelARN" in properties
                        and properties["CustomExtractionModelARN"].strip()
                    ):
                        if "extraction" in resolved_default:
                            resolved_default["extraction"]["model"] = properties[
                                "CustomExtractionModelARN"
                            ]
                            logger.info(
                                f"Updated extraction model to: {properties['CustomExtractionModelARN']}"
                            )

                configurations["Default"] = resolved_default

            # Process Custom configuration if provided and not empty
            if (
                "Custom" in properties
                and properties["Custom"].get("Info") != "Custom inference settings"
            ):
                resolved_custom = resolve_content(properties["Custom"])
                # Remove legacy pricing field if present (now stored separately as DefaultPricing)
                if isinstance(resolved_custom, dict):
                    resolved_custom.pop("pricing", None)
                configurations["Custom"] = resolved_custom

            # Process DefaultPricing configuration if provided
            if "DefaultPricing" in properties:
                resolved_pricing = resolve_content(properties["DefaultPricing"])
                # Pricing doesn't need region-specific filtering or swapping
                # as it includes all regions (US, EU, Global) in one file
                configurations["DefaultPricing"] = resolved_pricing
                logger.info("Loaded DefaultPricing configuration")

            # Apply region-specific model swapping to all configurations at once
            if region_type in ["us", "eu"] and configurations:
                configurations = swap_model_ids(configurations, region_type)
                logger.info(f"Applied model swapping for {region_type} region to all configurations")

            # Save all configurations
            for config_name, config_data in configurations.items():
                if config_name == "Default" and request_type == "Create":
                    # For new stacks, save Default as v0 with versioning metadata
                    import datetime
                    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
                    
                    metadata_v0 = {
                        "is_active": True,
                        "created_at": timestamp,
                        "description": "System default configuration (v0)"
                    }
                    manager.save_configuration("v0", config_data, metadata=metadata_v0)
                    logger.info("Created v0 from Default configuration for new stack")
                else:
                    # Save other configurations (Schema, DefaultPricing) normally
                    manager.save_configuration(config_name, config_data)
                    logger.info(f"Updated {config_name} configuration")

            cfnresponse.send(
                event,
                context,
                cfnresponse.SUCCESS,
                {"Message": f"Successfully {request_type.lower()}d configurations"},
                physical_id,
            )

        elif request_type == "Delete":
            # Do nothing on delete - preserve any existing configuration otherwise
            # data is lost during custom resource replacement (cleanup step), e.g.
            # if nested stack name or resource name is changed
            logger.info("Delete request received - preserving configuration (no-op)")
            cfnresponse.send(
                event,
                context,
                cfnresponse.SUCCESS,
                {"Message": "Success (delete = no-op)"},
                physical_id,
            )

    except ValidationError as e:
        # Pydantic validation error - format detailed error message
        logger.error(f"Configuration validation error: {e}")

        # Build detailed error message
        error_messages = []
        for error in e.errors():
            field_path = " -> ".join(str(loc) for loc in error["loc"])
            error_messages.append(
                f"{field_path}: {error['msg']} (type: {error['type']})"
            )

        detailed_error = "Configuration validation failed:\n" + "\n".join(
            error_messages
        )

        # Still need to send physical ID even on failure
        physical_id = generate_physical_id(event["StackId"], event["LogicalResourceId"])
        cfnresponse.send(
            event,
            context,
            cfnresponse.FAILED,
            {
                "Error": "ValidationError",
                "Message": detailed_error,
                "ValidationErrors": [
                    {
                        "field": " -> ".join(str(loc) for loc in err["loc"]),
                        "message": err["msg"],
                        "type": err["type"],
                    }
                    for err in e.errors()
                ],
            },
            physical_id,
            reason=detailed_error[:256],  # CloudFormation has 256 char limit for reason
        )

    except json.JSONDecodeError as e:
        # JSON parsing error
        logger.error(f"JSON decode error: {e}")
        error_message = (
            f"Invalid JSON format at line {e.lineno}, column {e.colno}: {str(e)}"
            if hasattr(e, "lineno")
            else f"Invalid JSON format: {str(e)}"
        )

        physical_id = generate_physical_id(event["StackId"], event["LogicalResourceId"])
        cfnresponse.send(
            event,
            context,
            cfnresponse.FAILED,
            {"Error": "JSONDecodeError", "Message": error_message},
            physical_id,
            reason=error_message[:256],
        )

    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        # Still need to send physical ID even on failure
        physical_id = generate_physical_id(event["StackId"], event["LogicalResourceId"])
        cfnresponse.send(
            event,
            context,
            cfnresponse.FAILED,
            {"Error": str(e)},
            physical_id,
            reason=str(e)[:256],
        )
