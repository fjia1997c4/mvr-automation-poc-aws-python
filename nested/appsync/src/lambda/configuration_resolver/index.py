# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from idp_common.config.configuration_manager import ConfigurationManager
from idp_common.config.models import SchemaConfig, IDPConfig, PricingConfig
from idp_common.config.constants import (
    CONFIG_TYPE_SCHEMA,
    CONFIG_TYPE_DEFAULT,
    CONFIG_TYPE_CUSTOM,
    CONFIG_TYPE_DEFAULT_PRICING,
    CONFIG_TYPE_CUSTOM_PRICING,
)
from pydantic import ValidationError
import os
import json
import logging

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
logging.getLogger("idp_common.bedrock.client").setLevel(
    os.environ.get("BEDROCK_LOG_LEVEL", "INFO")
)


def handler(event, context):
    """
    AWS Lambda handler for GraphQL operations related to configuration.

    Returns structured responses with success/error information:

    Success response:
    {
        "success": true,
        "Schema": {...},
        "Default": {...},
        "Custom": {...}
    }

    Error response:
    {
        "success": false,
        "error": {
            "type": "ValidationError" | "JSONDecodeError",
            "message": "...",
            "validationErrors": [...]  // if ValidationError
        }
    }
    """
    logger.info(f"Event received: {json.dumps(event)}")

    # Extract the GraphQL operation type
    operation = event["info"]["fieldName"]

    # Initialize ConfigurationManager
    manager = ConfigurationManager()

    try:
        if operation == "getConfigVersions":
            return handle_get_config_versions(manager)
        elif operation == "getConfigVersion":
            args = event["arguments"]
            version_id = args.get("versionId")
            return handle_get_config_version(manager, version_id)
        elif operation == "updateConfiguration":
            args = event["arguments"]
            version_id = args.get("versionId")
            custom_config = args.get("customConfig")
            return handle_update_configuration(manager, version_id, custom_config)
        elif operation == "setActiveVersion":
            args = event["arguments"]
            version_id = args.get("versionId")
            return handle_set_active_version(manager, version_id)
        elif operation == "saveAsNewVersion":
            args = event["arguments"]
            configuration = args.get("configuration")
            description = args.get("description")
            set_as_active = args.get("setAsActive", False)
            return handle_save_as_new_version(manager, configuration, description, set_as_active)
        elif operation == "getPricing":
            return handle_get_pricing(manager)
        elif operation == "updatePricing":
            args = event["arguments"]
            pricing_config = args.get("pricingConfig")
            return handle_update_pricing(manager, pricing_config)
        elif operation == "restoreDefaultPricing":
            return handle_restore_default_pricing(manager)
        elif operation == "listConfigurationLibrary":
            return handle_list_config_library(event["arguments"])
        elif operation == "getConfigurationLibraryFile":
            return handle_get_config_library_file(event["arguments"])
        else:
            raise Exception(f"Unsupported operation: {operation}")
    except ValidationError as e:
        # Pydantic validation error - return structured error for UI
        logger.error(f"Configuration validation error: {e}")

        # Build structured error response that UI can parse
        validation_errors = []
        for error in e.errors():
            field_path = " -> ".join(str(loc) for loc in error["loc"])
            validation_errors.append(
                {"field": field_path, "message": error["msg"], "type": error["type"]}
            )

        # Return error as data (not exception) so UI can handle it
        return {
            "success": False,
            "error": {
                "type": "ValidationError",
                "message": "Configuration validation failed",
                "validationErrors": validation_errors,
            },
        }

    except json.JSONDecodeError as e:
        # JSON parsing error - return structured error
        logger.error(f"JSON decode error: {e}")
        return {
            "success": False,
            "error": {
                "type": "JSONDecodeError",
                "message": f"Invalid JSON format: {str(e)}",
                "position": {
                    "line": e.lineno if hasattr(e, "lineno") else None,
                    "column": e.colno if hasattr(e, "colno") else None,
                },
            },
        }
    except Exception as e:
        # Catch all other exceptions to prevent lambda failures
        logger.error(f"Unexpected error in {operation}: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": {
                "type": "UnexpectedError",
                "message": f"An unexpected error occurred: {str(e)}",
            },
        }


def handle_get_configuration(manager):
    """
    Handle the getConfiguration GraphQL query
    Returns Schema, Default, and Custom configuration items with auto-migration support

    Data Flow:
    1. If Custom is empty on first read, copy Default → Custom
    2. Frontend only uses Custom for display and diffing
    3. Default is only used for "Reset to Default" operation

    New ConfigurationManager API returns IDPConfig directly - convert to dict for GraphQL
    """
    try:
        # Get all configurations - migration happens automatically in get_configuration
        # API returns SchemaConfig for Schema, IDPConfig for Default/Custom
        schema_config = manager.get_configuration(CONFIG_TYPE_SCHEMA)
        if schema_config:
            # Remove config_type discriminator before sending to frontend
            schema_dict = schema_config.model_dump(
                mode="python", exclude={"config_type"}
            )
        else:
            schema_dict = {}

        default_config = manager.get_configuration(CONFIG_TYPE_DEFAULT)
        if default_config and isinstance(default_config, IDPConfig):
            default_dict = default_config.model_dump(
                mode="python", exclude={"config_type"}
            )
        else:
            default_dict = {}

        custom_config = manager.get_configuration(CONFIG_TYPE_CUSTOM)

        # IMPORTANT: If Custom is empty on first read, copy Default → Custom
        # This ensures frontend always has a complete config to diff against
        if not custom_config or (
            isinstance(custom_config, IDPConfig)
            and not custom_config.model_dump(exclude_unset=True)
        ):
            logger.info("Custom config is empty, copying Default → Custom")
            if default_config and isinstance(default_config, IDPConfig):
                manager.save_configuration(CONFIG_TYPE_CUSTOM, default_config)
                custom_config = default_config
                logger.info("Copied Default to Custom on first read")
            else:
                logger.warning("Default config is also empty, using empty Custom")

        if custom_config and isinstance(custom_config, IDPConfig):
            custom_dict = custom_config.model_dump(
                mode="python", exclude={"config_type"}
            )
        else:
            custom_dict = {}

        # Return all configurations as dicts (GraphQL requires JSON-serializable)
        result = {
            "success": True,
            "Schema": schema_dict,
            "Default": default_dict,
            "Custom": custom_dict,
        }

        logger.info(f"Returning configuration")
        return result

    except Exception as e:
        logger.error(f"Error in getConfiguration: {str(e)}")
        raise e


def handle_list_config_library(args):
    """
    List available configurations from S3 config_library for a specific pattern
    Returns: { success: bool, items: [...], error: str }
    """
    import boto3
    from botocore.exceptions import ClientError

    pattern = args.get("pattern")
    if not pattern:
        return {"success": False, "items": [], "error": "Pattern parameter is required"}

    try:
        s3_client = boto3.client("s3")
        bucket_name = os.environ.get("CONFIGURATION_BUCKET")
        prefix = f"config_library/{pattern}/"

        logger.info(
            f"Listing config library for pattern: {pattern} in bucket: {bucket_name}"
        )

        # List "directories" under the pattern folder
        response = s3_client.list_objects_v2(
            Bucket=bucket_name, Prefix=prefix, Delimiter="/"
        )

        items = []

        # CommonPrefixes are the "directories" (config folders)
        for common_prefix in response.get("CommonPrefixes", []):
            config_dir = common_prefix["Prefix"]
            config_name = config_dir.rstrip("/").split("/")[-1]

            # Check if README.md exists in this config directory
            readme_key = f"{config_dir}README.md"
            has_readme = False

            try:
                s3_client.head_object(Bucket=bucket_name, Key=readme_key)
                has_readme = True
            except ClientError as e:
                if e.response["Error"]["Code"] != "404":
                    logger.warning(f"Error checking README for {config_name}: {e}")

            # Detect which config file type exists (prefer YAML, fallback to JSON)
            config_file_type = None
            yaml_key = f"{config_dir}config.yaml"
            json_key = f"{config_dir}config.json"

            try:
                s3_client.head_object(Bucket=bucket_name, Key=yaml_key)
                config_file_type = "yaml"
            except ClientError:
                # YAML doesn't exist, try JSON
                try:
                    s3_client.head_object(Bucket=bucket_name, Key=json_key)
                    config_file_type = "json"
                except ClientError:
                    logger.warning(
                        f"No config file found for {config_name} (checked yaml and json)"
                    )
                    # Skip this config if no config file exists
                    continue

            items.append({
                "name": config_name,
                "hasReadme": has_readme,
                "path": config_dir,
                "configFileType": config_file_type
            })

        if not items:
            logger.info(f"No configurations found for pattern: {pattern}")

        logger.info(f"Found {len(items)} configurations for pattern: {pattern}")
        return {"success": True, "items": items, "error": None}

    except ClientError as e:
        logger.error(f"S3 error listing config library: {e}")
        return {
            "success": False,
            "items": [],
            "error": f"Failed to list configurations: {str(e)}",
        }
    except Exception as e:
        logger.error(f"Error listing config library: {e}")
        return {
            "success": False,
            "items": [],
            "error": f"Unexpected error: {str(e)}",
        }


def handle_get_config_library_file(args):
    """
    Get a specific file (config.yaml or README.md) from config library
    Returns: { success: bool, content: str, contentType: str, error: str }
    """
    import boto3
    from botocore.exceptions import ClientError

    pattern = args.get("pattern")
    config_name = args.get("configName")
    file_name = args.get("fileName")

    if not all([pattern, config_name, file_name]):
        return {
            "success": False,
            "content": "",
            "contentType": "",
            "error": "Missing required parameters",
        }

    # Security: Only allow specific file names
    if file_name not in ["config.yaml", "config.json", "README.md"]:
        return {
            "success": False,
            "content": "",
            "contentType": "",
            "error": f"Invalid file name: {file_name}",
        }

    try:
        s3_client = boto3.client("s3")
        bucket_name = os.environ.get("CONFIGURATION_BUCKET")
        key = f"config_library/{pattern}/{config_name}/{file_name}"

        logger.info(f"Getting file from S3: {bucket_name}/{key}")

        response = s3_client.get_object(Bucket=bucket_name, Key=key)
        content = response["Body"].read().decode("utf-8")

        # Set appropriate content type based on file extension
        if file_name == "README.md":
            content_type = "text/markdown"
        elif file_name == "config.json":
            content_type = "application/json"
        else:
            content_type = "text/yaml"

        logger.info(
            f"Successfully retrieved {file_name} for {pattern}/{config_name} "
            f"({len(content)} bytes)"
        )
        return {
            "success": True,
            "content": content,
            "contentType": content_type,
            "error": None,
        }

    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            error_msg = f"File not found: {file_name}"
        else:
            error_msg = f"S3 error: {str(e)}"

        logger.error(f"Error getting config library file: {error_msg}")
        return {
            "success": False,
            "content": "",
            "contentType": "",
            "error": error_msg,
        }
    except Exception as e:
        logger.error(f"Error getting config library file: {e}")
        return {
            "success": False,
            "content": "",
            "contentType": "",
            "error": f"Unexpected error: {str(e)}",
        }


def handle_get_pricing(manager):
    """
    Handle the getPricing GraphQL query
    Returns both merged pricing and default pricing for UI diff/restore features

    This mirrors the Default/Custom pattern for IDP configuration:
    - DefaultPricing: Full baseline from deployment (stored at deployment time)
    - CustomPricing: User overrides only (deltas)
    - Returns:
      - pricing: Merged result (default + custom overrides)
      - defaultPricing: Original defaults for diff highlighting and restore

    Returns: { success: bool, pricing: {...}, defaultPricing: {...}, error: {...} }
    """
    try:
        # Get merged pricing (DefaultPricing + CustomPricing deltas)
        pricing_config = manager.get_merged_pricing()

        # Also get default pricing for UI diff/restore features
        default_pricing_config = manager.get_configuration(CONFIG_TYPE_DEFAULT_PRICING)

        empty_pricing = {
            "textract": {},
            "bedrock": {},
            "bda": {},
            "sagemaker": {},
        }

        if pricing_config and isinstance(pricing_config, PricingConfig):
            # Convert to dict, excluding config_type discriminator
            pricing_dict = pricing_config.model_dump(
                mode="python", exclude={"config_type"}
            )
            logger.info("Returning merged pricing configuration from DynamoDB")
        else:
            # No DefaultPricing in DynamoDB - this shouldn't happen after deployment
            logger.warning("No DefaultPricing found in DynamoDB")
            pricing_dict = empty_pricing

        if default_pricing_config and isinstance(default_pricing_config, PricingConfig):
            default_pricing_dict = default_pricing_config.model_dump(
                mode="python", exclude={"config_type"}
            )
            logger.info("Returning default pricing for UI diff/restore")
        else:
            logger.warning("No DefaultPricing found for diff/restore")
            default_pricing_dict = empty_pricing

        return {
            "success": True,
            "pricing": pricing_dict,
            "defaultPricing": default_pricing_dict,
        }

    except Exception as e:
        logger.error(f"Error in getPricing: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "Error",
                "message": f"Failed to get pricing: {str(e)}",
            },
        }


def handle_update_pricing(manager, pricing_config_json):
    """
    Handle the updatePricing GraphQL mutation
    Saves custom pricing overrides (deltas) to DynamoDB

    This saves to CustomPricing, which stores only user overrides.
    The overrides are merged with DefaultPricing when reading.

    Args:
        manager: ConfigurationManager instance
        pricing_config_json: JSON string or dict with pricing deltas

    Returns: { success: bool, message: str, error: {...} }
    """
    try:
        # Parse JSON if it's a string
        if isinstance(pricing_config_json, str):
            pricing_data = json.loads(pricing_config_json)
        else:
            pricing_data = pricing_config_json

        # Validate and create PricingConfig
        pricing_config = PricingConfig(**pricing_data)

        # Save to CustomPricing (deltas only)
        success = manager.save_custom_pricing(pricing_config)

        if success:
            logger.info("Custom pricing configuration updated successfully")
            return {
                "success": True,
                "message": "Pricing configuration updated successfully",
            }
        else:
            return {
                "success": False,
                "message": "Failed to save pricing configuration",
                "error": {
                    "type": "SaveError",
                    "message": "Failed to save pricing configuration to database",
                },
            }

    except ValidationError as e:
        logger.error(f"Pricing validation error: {e}")
        validation_errors = []
        for error in e.errors():
            field_path = " -> ".join(str(loc) for loc in error["loc"])
            validation_errors.append(
                {"field": field_path, "message": error["msg"], "type": error["type"]}
            )
        return {
            "success": False,
            "error": {
                "type": "ValidationError",
                "message": "Pricing validation failed",
                "validationErrors": validation_errors,
            },
        }

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in pricing: {e}")
        return {
            "success": False,
            "error": {
                "type": "JSONDecodeError",
                "message": f"Invalid JSON format: {str(e)}",
            },
        }

    except Exception as e:
        logger.error(f"Error in updatePricing: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "Error",
                "message": f"Failed to update pricing: {str(e)}",
            },
        }


def handle_restore_default_pricing(manager):
    """
    Handle the restoreDefaultPricing GraphQL mutation
    Restores pricing to the default values by deleting CustomPricing

    This simply deletes the CustomPricing record from DynamoDB.
    After deletion, get_merged_pricing() returns DefaultPricing only.

    Returns: { success: bool, message: str, error: {...} }
    """
    try:
        # Delete CustomPricing - this effectively resets to defaults
        success = manager.delete_custom_pricing()

        if success:
            logger.info("Pricing restored to default by deleting CustomPricing")
            return {
                "success": True,
                "message": "Pricing restored to default values",
            }
        else:
            return {
                "success": False,
                "message": "Failed to restore default pricing",
                "error": {
                    "type": "DeleteError",
                    "message": "Failed to delete custom pricing from database",
                },
            }

    except Exception as e:
        logger.error(f"Error in restoreDefaultPricing: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "Error",
                "message": f"Failed to restore default pricing: {str(e)}",
            },
        }


def handle_get_config_versions(manager):
    """
    Handle the getConfigVersions GraphQL query
    Returns list of all available configuration versions
    """
    try:
        versions = []
        
        # Get all configuration items from DynamoDB
        response = manager.table.scan(
            ProjectionExpression="Configuration, CreatedAt, Description, IsActive"
        )
        
        for item in response.get('Items', []):
            config_key = item.get('Configuration', {}).get('S', '')
            
            # Skip non-version items (Schema, pricing, etc.)
            if config_key in ['Schema', 'DefaultPricing', 'CustomPricing']:
                continue
                
            # Handle versioned configurations (v0, v1, v2, etc.)
            if config_key.startswith('v') and config_key[1:].isdigit():
                versions.append({
                    "versionId": config_key,
                    "isActive": item.get('IsActive', {}).get('BOOL', False),
                    "createdAt": item.get('CreatedAt', {}).get('S'),
                    "description": item.get('Description', {}).get('S', f"Configuration version {config_key}")
                })
            # Handle legacy Default/Custom (fallback for incomplete migrations)
            elif config_key == 'Default':
                versions.append({
                    "versionId": "v0",
                    "isActive": item.get('IsActive', {}).get('BOOL', True),
                    "createdAt": item.get('CreatedAt', {}).get('S'),
                    "description": "System default configuration (v0)"
                })
            elif config_key == 'Custom':
                versions.append({
                    "versionId": "v1", 
                    "isActive": item.get('IsActive', {}).get('BOOL', False),
                    "createdAt": item.get('CreatedAt', {}).get('S'),
                    "description": "User customized configuration (v1)"
                })
        
        return {
            "success": True,
            "versions": versions
        }
        
    except Exception as e:
        logger.error(f"Error in getConfigVersions: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "Error",
                "message": f"Failed to get configuration versions: {str(e)}",
            },
        }


def handle_get_config_version(manager, version_id):
    """
    Handle the getConfigVersion GraphQL query
    Returns a specific configuration version
    """
    try:
        if not version_id:
            return {
                "success": False,
                "error": {
                    "type": "ValidationError",
                    "message": "versionId is required",
                },
            }
        
        # Get schema (same for all versions)
        schema_config = manager.get_configuration(CONFIG_TYPE_SCHEMA)
        if schema_config:
            schema_dict = schema_config.model_dump(mode="python", exclude={"config_type"})
        else:
            schema_dict = {}
        
        # Get the requested version directly
        config = manager.get_configuration(version_id)
        
        if not config:
            return {
                "success": False,
                "error": {
                    "type": "NotFoundError",
                    "message": f"Configuration version '{version_id}' not found",
                },
            }
        
        if isinstance(config, IDPConfig):
            config_dict = config.model_dump(mode="python", exclude={"config_type"})
        else:
            config_dict = {}
        
        return {
            "success": True,
            "Schema": schema_dict,
            "Configuration": config_dict
        }
        
    except Exception as e:
        logger.error(f"Error in getConfigVersion: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "Error",
                "message": f"Failed to get configuration version: {str(e)}",
            },
        }


def handle_update_configuration(manager, version_id, custom_config):
    """
    Handle the updateConfiguration GraphQL mutation
    Updates a specific configuration version
    """
    try:
        if not version_id:
            return {
                "success": False,
                "error": {
                    "type": "ValidationError",
                    "message": "versionId is required",
                },
            }
        
        # Update the specific version
        success = manager.handle_update_custom_configuration(custom_config, version_id)
        
        return {
            "success": success,
            "message": f"Configuration version {version_id} updated successfully"
            if success
            else f"Failed to update configuration version {version_id}",
        }
        
    except Exception as e:
        logger.error(f"Error in updateConfiguration: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "Error",
                "message": f"Failed to update configuration: {str(e)}",
            },
        }


def handle_set_active_version(manager, version_id):
    """
    Handle the setActiveVersion GraphQL mutation
    Sets a specific version as active and deactivates others
    """
    try:
        if not version_id:
            return {
                "success": False,
                "error": {
                    "type": "ValidationError",
                    "message": "versionId is required",
                },
            }
        
        # Check if the version exists
        config = manager.get_configuration(version_id)
        if not config:
            return {
                "success": False,
                "error": {
                    "type": "NotFoundError",
                    "message": f"Configuration version '{version_id}' not found",
                },
            }
        
        # Get all versions and update IsActive flags
        response = manager.table.scan(
            ProjectionExpression="Configuration"
        )
        
        for item in response.get('Items', []):
            config_key = item.get('Configuration', {}).get('S', '')
            
            # Skip non-version items
            if config_key in ['Schema', 'DefaultPricing', 'CustomPricing']:
                continue
                
            # Update IsActive flag
            is_active = (config_key == version_id)
            manager.table.update_item(
                Key={'Configuration': config_key},
                UpdateExpression='SET IsActive = :active',
                ExpressionAttributeValues={':active': is_active}
            )
        
        return {
            "success": True,
            "message": f"Configuration version {version_id} set as active",
        }
        
    except Exception as e:
        logger.error(f"Error in setActiveVersion: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "Error",
                "message": f"Failed to set active version: {str(e)}",
            },
        }


def handle_save_as_new_version(manager, configuration, description, set_as_active):
    """
    Handle the saveAsNewVersion GraphQL mutation
    Creates a new version with auto-incremented version ID
    """
    try:
        import json
        import datetime
        
        if not configuration:
            return {
                "success": False,
                "error": {
                    "type": "ValidationError",
                    "message": "configuration is required",
                },
            }
        
        # Get all existing versions to find the next version number
        response = manager.table.scan(
            ProjectionExpression="Configuration"
        )
        
        max_version = -1
        for item in response.get('Items', []):
            config_key = item.get('Configuration', {}).get('S', '')
            
            # Check for versioned configurations (v0, v1, v2, etc.)
            if config_key.startswith('v') and config_key[1:].isdigit():
                version_num = int(config_key[1:])
                max_version = max(max_version, version_num)
        
        # Generate next version ID
        next_version = f"v{max_version + 1}"
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        
        # Parse configuration
        if isinstance(configuration, str):
            config_data = json.loads(configuration)
        else:
            config_data = configuration
        
        # Prepare metadata separately
        metadata = {
            "is_active": set_as_active,
            "created_at": timestamp,
            "description": description or f"Configuration version {next_version}"
        }
        
        # Save new version
        manager.save_configuration(next_version, config_data, metadata=metadata)
        
        # If setting as active, deactivate other versions
        if set_as_active:
            for item in response.get('Items', []):
                config_key = item.get('Configuration', {}).get('S', '')
                
                # Skip non-version items and the new version
                if config_key in ['Schema', 'DefaultPricing', 'CustomPricing'] or config_key == next_version:
                    continue
                    
                # Deactivate other versions
                manager.table.update_item(
                    Key={'Configuration': config_key},
                    UpdateExpression='SET IsActive = :active',
                    ExpressionAttributeValues={':active': False}
                )
        
        return {
            "success": True,
            "message": f"Configuration saved as {next_version}" + 
                      (" and set as active" if set_as_active else ""),
        }
        
    except Exception as e:
        logger.error(f"Error in saveAsNewVersion: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "Error",
                "message": f"Failed to save new version: {str(e)}",
            },
        }
