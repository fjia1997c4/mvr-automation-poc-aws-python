# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import boto3
import json
import os
from typing import Dict, Any, Optional, Union, List
from botocore.exceptions import ClientError
import logging

from .models import IDPConfig, SchemaConfig, PricingConfig, ConfigurationRecord, ConfigMetadata
from .merge_utils import (
    deep_update,
    apply_delta_with_deletions,
    strip_matching_defaults,
    get_diff_dict,
)
from .constants import (
    CONFIG_TYPE_CUSTOM_PRICING,
    CONFIG_TYPE_DEFAULT_PRICING,
    CONFIG_TYPE_SCHEMA,
    CONFIG_TYPE_CONFIG,
    VALID_CONFIG_TYPES,
    DEFAULT_VERSION
)

logger = logging.getLogger(__name__)

class ConfigurationManager:
    """
    Manages IDP configurations stored in DynamoDB.

    All operations use IDPConfig (Pydantic models) - no dict manipulation!
    ConfigurationRecord handles DynamoDB serialization internally.

    Example:
        manager = ConfigurationManager()

        # Get configuration (always returns IDPConfig)
        config = manager.get_configuration(CONFIG_TYPE_DEFAULT)

        # Save configuration
        manager.save_configuration(CONFIG_TYPE_CUSTOM, config)
    """

    def __init__(self, table_name: Optional[str] = None):
        """
        Initialize the configuration manager.

        Args:
            table_name: Optional override for configuration table name.
                       If not provided, uses CONFIGURATION_TABLE_NAME env var.

        Raises:
            ValueError: If table name cannot be determined
        """
        table_name = table_name or os.environ.get("CONFIGURATION_TABLE_NAME")
        if not table_name:
            raise ValueError(
                "Configuration table name not provided. Either set CONFIGURATION_TABLE_NAME "
                "environment variable or provide table_name parameter."
            )

        self.dynamodb = boto3.resource("dynamodb")
        self.table = self.dynamodb.Table(
            table_name
        )  # pyright: ignore[reportAttributeAccessIssue]
        self.table_name = table_name
        logger.info(f"ConfigurationManager initialized with table: {table_name}")

    def get_configuration(
        self, config_type: str, version: Optional[str] = None
    ) -> Optional[Union[SchemaConfig, IDPConfig, PricingConfig]]:
        """
        Retrieve configuration from DynamoDB.

        This method:
        1. Reads the DynamoDB item
        2. Deserializes into ConfigurationRecord (auto-migrates legacy format)
        3. Checks if migration occurred and persists if needed
        4. Returns SchemaConfig for Schema type, PricingConfig for Pricing, IDPConfig for Version

        Args:
            config_type: Configuration type (Schema, Config, Pricing)

        Returns:
            SchemaConfig for Schema type, PricingConfig for Pricing, IDPConfig for version, or None if not found

        Raises:
            ClientError: If DynamoDB operation fails

        """
        try:
            record = self._read_record(config_type, version=version)
            if record is None:
                logger.info(f"Configuration not found: {config_type}")
                return None

            # Note: ConfigurationRecord.from_dynamodb_item() auto-migrates legacy format
            # We don't need to check for migration separately - it's already done
            # If we want to persist the migration, we can optionally do so here

            return record.config

        except ClientError as e:
            logger.error(f"Error retrieving configuration {config_type}: {e}")
            raise

    def get_raw_configuration(self, config_type: str, version: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve RAW configuration from DynamoDB without Pydantic validation.
        
        This is critical for the Custom configuration which should return ONLY
        the user-modified fields (sparse delta), NOT a full config with Pydantic defaults.

        Design Pattern:
        - Version item stores ONLY user deltas
        - Using Pydantic validation would fill in all defaults (BAD for delta pattern)
        - This method returns the raw dict exactly as stored in DynamoDB

        Args:
            config_type: Configuration type (typically CONFIG_TYPE_CONFIG)
            
        Returns:
            Raw dict from DynamoDB (without Pydantic default-filling), or None if not found

        Raises:
            ClientError: If DynamoDB operation fails
        """
        try:
            if version:
                item = {"Configuration": f"{config_type}#{version}"}
            else:
                item = {"Configuration": config_type}

            response = self.table.get_item(Key=item)
            item = response.get("Item")

            if item is None:
                logger.info(f"Raw configuration not found: {config_type}")
                return None

            # Remove the DynamoDB partition key - return only the config data
            config_data = {k: v for k, v in item.items() if k != "Configuration"}

            logger.info(f"Retrieved raw configuration for {config_type}")
            return config_data

        except ClientError as e:
            logger.error(f"Error retrieving raw configuration {config_type}: {e}")
            raise

    def save_raw_configuration(self, config_type: str, config_dict: Dict[str, Any], version: str, description: Optional[str] = None) -> None:
        """
        Save raw configuration dict to DynamoDB WITHOUT Pydantic validation.
        
        This is critical for Version configs which should store ONLY user deltas (sparse).
        Using Pydantic would fill in all defaults, which defeats the delta pattern.
        
        WARNING: Only use for CONFIG_TYPE_CONFIG to preserve sparse delta pattern for non default version.
        For other config types, use save_configuration() which
        validates through Pydantic.

        Args:
            config_type: Configuration type (should be CONFIG_TYPE_CONFIG)
            config_dict: Raw dict to save (only user deltas, no defaults)
            version: Version to save
            
        Raises:
            ClientError: If DynamoDB operation fails
        """
        try:
            # Build DynamoDB item directly without Pydantic
            # IMPORTANT: Stringify values to convert floats to strings for DynamoDB
            # (DynamoDB doesn't accept Python float types, only Decimal or string)
            if version:
                item = {"Configuration": f"{config_type}#{version}"}
            else:
                item = {"Configuration": config_type}
            
            # Add metadata for version configurations
            if version and config_type == CONFIG_TYPE_CONFIG:
                from datetime import datetime
                current_time = datetime.utcnow().isoformat() + 'Z'
                
                # Check if this is a new version (no existing record)
                existing_record = self._read_record(config_type, version)
                if not existing_record:
                    # New version - set creation time
                    item["CreatedAt"] = current_time
                    item["UpdatedAt"] = current_time
                    item["IsActive"] = False  # New versions are not active by default
                    # Set description (empty string if None provided)
                    item["Description"] = description if description is not None else ""
                else: # existing record
                    # Always update the modification time
                    item["UpdatedAt"] = current_time
                    if description is not None:
                        item["Description"] = description
                
            stringified = ConfigurationRecord._stringify_values(config_dict)
            item.update(stringified)

            self.table.put_item(Item=item)
            logger.info(f"Saved raw configuration (sparse delta): {config_type}, version: {version}")
            
        except ClientError as e:
            logger.error(f"Error saving raw configuration {config_type}: {e}")
            raise

    def get_merged_configuration(self, version: str) -> Optional[IDPConfig]:
        """
        Get merged Default + Version configuration for runtime processing.

        This is THE method to use for all runtime document processing.
        It properly merges the stack defaul with version deltas.

        Design Pattern:
        - default = complete stack baseline (from deployment)
        - version = sparse user deltas ONLY
        - Merged = Default deep-updated with version = final runtime config

        Returns:
            Merged IDPConfig ready for runtime use, or None if default doesn't exist

        Raises:
            ClientError: If DynamoDB operation fails
            ValueError: If version not found
        """
        from copy import deepcopy

        # Get the full Default configuration (Pydantic validated - this is correct)
        default_config = self.get_configuration(CONFIG_TYPE_CONFIG, DEFAULT_VERSION)
        if default_config is None:
            logger.warning(
                "Default configuration not found - cannot create merged config"
            )
            return None

        if not isinstance(default_config, IDPConfig):
            logger.error(f"Default config is not IDPConfig: {type(default_config)}")
            return None

        if not version:
            raise ValueError("version not provided")
        
        # Get version as RAW dict (no Pydantic defaults!)
        version_dict = self.get_raw_configuration(CONFIG_TYPE_CONFIG, version)

        # If no Custom, return Default as-is
        if not version_dict:
            raise ValueError(f"No Version {version} configuration found")
        
        # Merge: Start with Default, deep update with version deltas
        default_dict = default_config.model_dump(mode="python")
        merged_dict = deepcopy(default_dict)
        deep_update(merged_dict, version_dict)

        logger.info("Merged default + version configurations for runtime")
        return IDPConfig(**merged_dict)


    def sync_custom_with_new_default(
        self, old_default: IDPConfig, new_default: IDPConfig, old_custom: IDPConfig
    ) -> IDPConfig:
        """
        Sync Custom config when Default is updated, preserving user customizations.

        Algorithm:
        1. Find what the user customized (diff between old_custom and old_default)
        2. Start with new_default
        3. Apply user customizations to new_default

        This ensures users get all new default values except for fields they customized.

        Args:
            old_default: Previous default configuration
            new_default: New default configuration being saved
            old_custom: Current custom configuration

        Returns:
            New custom configuration with user changes preserved
        """
        from copy import deepcopy

        # Convert to dicts
        old_default_dict = old_default.model_dump(mode="python")
        old_custom_dict = old_custom.model_dump(mode="python")
        new_default_dict = new_default.model_dump(mode="python")

        # Find what the user customized (only fields that differ)
        user_customizations = get_diff_dict(old_default_dict, old_custom_dict)

        logger.info(
            f"User customizations to preserve: {list(user_customizations.keys())}"
        )

        # Start with new default and apply user customizations
        new_custom_dict = deepcopy(new_default_dict)
        deep_update(new_custom_dict, user_customizations)

        return IDPConfig(**new_custom_dict)

    def save_configuration(
        self,
        config_type: str,
        config: Union[SchemaConfig, IDPConfig, PricingConfig, Dict[str, Any]],
        version: Optional[str] = None,
        description: Optional[str] = None,
        skip_sync: bool = False,
    ) -> None:
        """
        Save configuration to DynamoDB.

        This method:
        1. Converts dict to appropriate config type if needed
        2. If saving default, syncs Version to preserve user customizations (unless skip_sync=True)
        3. Creates ConfigurationRecord
        4. Serializes to DynamoDB item
        5. Writes to DynamoDB

        Args:
            config_type: Configuration type (Schema, Config, DefaultPricing, CustomPricing)
            config: SchemaConfig, IDPConfig, PricingConfig model, or dict (dict will be converted to appropriate type)
            skip_sync: If True, skip automatic Version sync
            skip_sync: should be false when saving default to auto sync verisons with default

        Raises:
            ClientError: If DynamoDB operation fails

        """
        # Convert dict to appropriate config type if needed (for backward compatibility)
        if isinstance(config, dict):
            if config_type == CONFIG_TYPE_SCHEMA:
                config = SchemaConfig(**config)
            elif config_type in (
                CONFIG_TYPE_DEFAULT_PRICING,
                CONFIG_TYPE_CUSTOM_PRICING,
            ):
                config = PricingConfig(**config)
            else:
                config = IDPConfig(**config)

        # If updating Default, sync versions to preserve user customizations
        if (
            config_type == CONFIG_TYPE_CONFIG
            and version == DEFAULT_VERSION
            and not skip_sync
            and isinstance(config, IDPConfig)
        ):
            old_default = self.get_configuration(CONFIG_TYPE_CONFIG, DEFAULT_VERSION)
            versions = self.list_config_versions()
            for version_dict in versions:
                version_name: Optional[str] = str(version_dict.get("versionName")) if version_dict.get("versionName") else None
                old_version = self.get_configuration(CONFIG_TYPE_CONFIG, version=version_name)
                if (
                    old_default
                    and version_name
                    and version_name != DEFAULT_VERSION
                    and isinstance(old_default, IDPConfig)
                    and isinstance(old_version, IDPConfig)
                ):
                    logger.info(
                        f"Syncing Version: {version_name} config with new Default while preserving user customizations"
                    )
                    new_version = self.sync_custom_with_new_default(
                        old_default, config, old_version
                    )
                    # Save the synced version config
                    self.save_configuration(CONFIG_TYPE_CONFIG, new_version, version=version_name, skip_sync=True)

        if config_type == CONFIG_TYPE_CONFIG:
            # get existing record if saving existing version
            existing_record = self._read_record(CONFIG_TYPE_CONFIG, version)
            is_active_status = existing_record.is_active if existing_record else False
            # update meta
            import datetime
            timestamp = datetime.datetime.utcnow().isoformat() + "Z"
            
            if existing_record:
                # Existing config - preserve created_at, update updated_at
                metadata = {
                    "created_at": existing_record.metadata.created_at if existing_record.metadata else timestamp,
                    "updated_at": timestamp
                }
                # Create record
                record = ConfigurationRecord(
                    configuration_type=config_type,
                    version=version,
                    is_active=is_active_status,  # Preserve existing active status or None for new
                    description=description if description else existing_record.description,
                    config=config,
                    metadata=ConfigMetadata(**metadata)
                )
            else:
                # New config - set both timestamps
                metadata = {
                    "created_at": timestamp,
                    "updated_at": timestamp
                }
                # Create record
                record = ConfigurationRecord(
                    configuration_type=config_type,
                    version=version,
                    is_active=is_active_status,  # Preserve existing active status or None for new
                    description=description,
                    config=config,
                    metadata=ConfigMetadata(**metadata)
                )
        else:
            # Create record
            record = ConfigurationRecord(configuration_type=config_type, config=config)
        
        # Write to DynamoDB
        self._write_record(record)

    def activate_version(self, version: str) -> None:
        """
        Activate a specific Config version and deactivate all others.
        
        Args:
            version: Version to activate (default, production-config, test-config, etc.)
            
        Raises:
            ValueError: If version doesn't exist
            ClientError: If DynamoDB operation fails
        """
        try:
            # First, verify the version exists
            target_record = self._read_record(CONFIG_TYPE_CONFIG, version)
            if not target_record:
                raise ValueError(f"Config version {version} not found")
            
            # Read all config record and update isactive = False if active
            for version_dict in self.list_config_versions():
                version_record = self._read_record(CONFIG_TYPE_CONFIG, version=version_dict.get("versionName"))
                if version_record and version_record.is_active:
                    version_record.is_active = False
                    self._write_record(version_record)

            # Update target version record as active = True
            target_record.is_active = True
            self._write_record(target_record)
            logger.info(f"Activated Config version {version}")
        except ClientError as e:
            logger.error(f"Error activating version {version}: {e}")
            raise

    def list_config_versions(self) -> List[Dict[str, Any]]:
        """
        List all configuration versions.
        
        Returns:
            List of version info dicts with versionId, isActive, createdAt, updatedAt, description
        """
        try:
            response = self.table.scan(
                FilterExpression="begins_with(Configuration, :config_prefix)",
                ExpressionAttributeValues={":config_prefix": f"{CONFIG_TYPE_CONFIG}#"},
                ProjectionExpression="Configuration, IsActive, CreatedAt, UpdatedAt, Description"
            )
            
            versions = []
            for item in response.get('Items', []):
                config_key = item.get('Configuration', '')
                if "#" in config_key:
                    _, version = config_key.split("#", 1)
                    versions.append({
                        "versionName": version,
                        "isActive": item.get('IsActive'),  # Can be None, True, or False
                        "createdAt": item.get('CreatedAt'),
                        "updatedAt": item.get('UpdatedAt'),
                        "description": item.get('Description', "")  # Return empty string instead of generated text
                    })
            
            return versions
            
        except ClientError as e:
            logger.error(f"Error listing config versions: {e}")
            return []

    def delete_configuration(self, config_type: str, version: Optional[str] = None) -> None:
        """
        Delete configuration from DynamoDB.

        Args:
            config_type: Configuration type to delete
            version: config version [applicable to config type: Config]
        Raises:
            ClientError: If DynamoDB operation fails
            ValueError: If version is required but not provided
        """
        try:
            if config_type == CONFIG_TYPE_CONFIG:
                if version is None:
                    raise ValueError("Version is required for Config type")
        
                # Check if trying to delete active version
                record = self._read_record(CONFIG_TYPE_CONFIG, version)
                logger.info(f"Checking version {version} for deletion. Record found: {record is not None}, Is active: {record.is_active if record else 'N/A'}")
                if not record:
                    raise ClientError(f"Version: {version} not found in configurations")
                if record and record.is_active:
                    raise ValueError(f"Cannot delete active version {version}. Activate another version first.")
                key = f"{CONFIG_TYPE_CONFIG}#{version}"
            else:
                # For all other types, use config_type directly
                key = config_type
            self.table.delete_item(Key={"Configuration": key})
            logger.info(f"Deleted configuration: {key}")
        except ClientError as e:
            logger.error(f"Error deleting configuration {config_type}: {e}")
            raise

    # ===== Pricing Configuration Methods =====

    def get_merged_pricing(self) -> Optional[PricingConfig]:
        """
        Get the merged pricing configuration (DefaultPricing + CustomPricing deltas).

        This mirrors the Default/Custom pattern for IDP configuration:
        - DefaultPricing: Full baseline pricing from deployment
        - CustomPricing: Only user overrides/deltas (if any)

        Returns:
            Merged PricingConfig with custom overrides applied, or None if not found

        Raises:
            ClientError: If DynamoDB operation fails
        """
        from copy import deepcopy

        # Get default pricing
        default_config = self.get_configuration(CONFIG_TYPE_DEFAULT_PRICING)
        if default_config is None:
            logger.warning("DefaultPricing not found in DynamoDB")
            return None

        if not isinstance(default_config, PricingConfig):
            logger.warning(
                f"Expected PricingConfig but got {type(default_config).__name__}"
            )
            return None

        # Get custom pricing (deltas only)
        custom_config = self.get_configuration(CONFIG_TYPE_CUSTOM_PRICING)

        # If no custom pricing, return default
        if custom_config is None:
            logger.info("No CustomPricing found, returning DefaultPricing")
            return default_config

        if not isinstance(custom_config, PricingConfig):
            logger.warning(
                f"CustomPricing is not PricingConfig, returning DefaultPricing"
            )
            return default_config

        # Merge: Start with default, apply custom overrides
        default_dict = default_config.model_dump(mode="python")
        custom_dict = custom_config.model_dump(mode="python")

        merged_dict = deepcopy(default_dict)
        deep_update(merged_dict, custom_dict)

        logger.info("Merged DefaultPricing with CustomPricing deltas")
        return PricingConfig(**merged_dict)

    def save_custom_pricing(
        self, pricing_deltas: Union[PricingConfig, Dict[str, Any]]
    ) -> bool:
        """
        Save custom pricing overrides to DynamoDB.

        This saves only the user's customizations (deltas from default).
        The deltas are merged with DefaultPricing when reading.

        Args:
            pricing_deltas: PricingConfig or dict with only the fields that differ from default

        Returns:
            True on success

        Raises:
            ClientError: If DynamoDB operation fails
        """
        # Convert dict to PricingConfig if needed
        if isinstance(pricing_deltas, dict):
            pricing_deltas = PricingConfig(**pricing_deltas)

        # Save to CustomPricing
        self.save_configuration(CONFIG_TYPE_CUSTOM_PRICING, pricing_deltas)

        logger.info("Saved CustomPricing configuration")
        return True

    def delete_custom_pricing(self) -> bool:
        """
        Delete custom pricing, effectively resetting to defaults.

        After deletion, get_merged_pricing() will return DefaultPricing only.

        Returns:
            True on success

        Raises:
            ClientError: If DynamoDB operation fails
        """
        try:
            self.delete_configuration(CONFIG_TYPE_CUSTOM_PRICING)
            logger.info("Deleted CustomPricing, pricing reset to defaults")
            return True
        except ClientError as e:
            # If the item doesn't exist, that's fine - it's already "deleted"
            if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                logger.info("CustomPricing already deleted or never existed")
                return True
            raise

    def handle_update_custom_configuration(
        self, custom_config: Union[str, Dict[str, Any], IDPConfig], version: Optional[str] = None, description: Optional[str] = None
    ) -> bool:
        """
        Handle the updateConfiguration GraphQL mutation.

        DESIGN PATTERN (CRITICAL):
        - Version stores ONLY user deltas (sparse)
        - Frontend sends deltas to merge into existing Version
        - We DO NOT use Pydantic defaults when reading existing Version

        Operations:
        - resetToDefault=True: Wont delete Version, no delta's to store
        - saveAsDefault=True [OPTION removed], in versioning default can't be modified
        - Normal update: Merge deltas into existing Version (raw, no Pydantic)

        Args:
            custom_config: Configuration deltas as JSON string, dict, or IDPConfig

        Returns:
            True on success

        Raises:
            Exception: If configuration update fails
        """

        # Parse input
        if isinstance(custom_config, str):
            config_dict = json.loads(custom_config)
        elif isinstance(custom_config, IDPConfig):
            config_dict = custom_config.model_dump(mode="python")
        else:
            config_dict = custom_config if custom_config else {}

        # Extract special flags before processing
        save_as_default = (
            config_dict.pop("saveAsDefault", False)
            if isinstance(config_dict, dict)
            else False
        )
        reset_to_default = (
            config_dict.pop("resetToDefault", False)
            if isinstance(config_dict, dict)
            else False
        )
        save_as_version = (
            config_dict.pop("saveAsVersion", False)
            if isinstance(config_dict, dict)
            else False
        )
        
        # Remove legacy pricing field if present (now stored separately as DefaultPricing/CustomPricing)
        if isinstance(config_dict, dict):
            config_dict.pop("pricing", None)

        # Handle reset to default - Version wont be deleted
        # Empty Version = use all defaults (this is the expected behavior)
        if reset_to_default:
            logger.info(f"Resetting version {version} to default")
            try:
                # save empty config on version [as no delta is applicable]
                self.save_raw_configuration(CONFIG_TYPE_CONFIG, None, version=version, description=description)
            except Exception as e:
                logger.info(f"Failed to resert version {version} to default: {e}")
            logger.info("Version {version} reset done - all defaults will now be used")
            return True
        
        # For empty config without special flags, nothing to do
        if not config_dict or (isinstance(config_dict, dict) and len(config_dict) == 0):
            logger.info(
                "Empty configuration update with no special flags - no changes made"
            )
            return True

        if save_as_default:
            # Save as Default: Frontend sends the complete merged config
            # This becomes the new baseline, saved over version
            config = IDPConfig(**config_dict)

            # sync required as we need to sync other non default versions
            self.save_configuration(CONFIG_TYPE_CONFIG, config, version=DEFAULT_VERSION, skip_sync=False)
            
            logger.info(f"Saved current state version: {version} as new {DEFAULT_VERSION}, version: {version} unchanged")
        elif save_as_version: # create new version
            # Save as new version (used for import operations)
            # Imported config is already merged with system defaults by frontend/import process
            logger.info(
                f"Save config as new version: {version}"
            )

            # Validate that Default + imported config creates a valid config
            default_config = self.get_configuration(CONFIG_TYPE_CONFIG, DEFAULT_VERSION)
            if default_config and isinstance(default_config, IDPConfig):
                from copy import deepcopy

                default_dict = default_config.model_dump(mode="python")
                validation_dict = deepcopy(default_dict)
                deep_update(validation_dict, config_dict)
                # This validates the merged config is valid - will raise ValidationError if not
                IDPConfig(**validation_dict)
                logger.info("Validated merged Default + imported configuration")

                # AUTO-CLEANUP: Remove fields that match their Default equivalents
                strip_matching_defaults(config_dict, default_dict)
                logger.info(
                    "Auto-cleaned imported config (removed values matching defaults)"
                )

            # Save ONLY the sparse Custom deltas (NO Pydantic defaults!)
            self.save_raw_configuration(CONFIG_TYPE_CONFIG, config_dict, version=version, description=description)
            logger.info(f"Save as new version: {version} configuration with imported config")
        else: 
            # Normal version config update - merge deltas into existing version
            # IMPORTANT: Use RAW version (no Pydantic defaults!) to preserve sparse pattern
            existing_version_dict = self.get_raw_configuration(CONFIG_TYPE_CONFIG, version=version)

            # If Custom doesn't exist, start with empty dict (NOT Default!)
            # Custom should only contain user deltas
            if existing_version_dict is None:
                existing_version_dict = {}
                logger.info(f"No existing verison {version} - creating new sparse delta config")

            # Merge the new deltas into existing version deltas
            # IMPORTANT: Use apply_delta_with_deletions to handle null values as deletions
            # This supports "reset to default" for individual fields:
            # - Frontend sends {"classification": {"model": null}}
            # - Backend removes "model" from Custom.classification
            # - When merged with Default, the Default value is used
            apply_delta_with_deletions(existing_version_dict, config_dict)

            # Validate that Default + merged Version creates a valid config
            # (but don't save the merged version - save only the sparse Custom)
            default_config = self.get_configuration(CONFIG_TYPE_CONFIG, DEFAULT_VERSION)
            if default_config and isinstance(default_config, IDPConfig):
                from copy import deepcopy

                default_dict = default_config.model_dump(mode="python")
                validation_dict = deepcopy(default_dict)
                deep_update(validation_dict, existing_version_dict)
                # This validates the merged config is valid - will raise ValidationError if not
                IDPConfig(**validation_dict)
                logger.info("Validated merged Default + Custom configuration")

                # AUTO-CLEANUP: Remove Custom fields that match their Default equivalents
                # This implements "self-healing" for sparse delta pattern:
                # - If user sets a value to its default, remove it from Custom
                # - Handles "restore to default" naturally (just set the default value)
                # - Keeps Custom truly sparse (only real customizations)
                strip_matching_defaults(existing_version_dict, default_dict)
                logger.info(
                    "Auto-cleaned Custom config (removed values matching defaults)"
                )

            # Save ONLY the sparse Custom deltas (NO Pydantic defaults!)
            self.save_raw_configuration(CONFIG_TYPE_CONFIG, config_dict=existing_version_dict, version = version, description=description)
            logger.info("Updated Custom configuration by merging deltas (sparse save)")

        return True


    def _read_record(self, configuration_type: str, version: str = "") -> Optional[ConfigurationRecord]:
        """
        Read ConfigurationRecord from DynamoDB using single key.

        Args:
            configuration_type: Configuration type (Config, Schema, Pricing)
            version: Version identifier for Config type (default, production-config, test-config, ...) or "" for Schema/Pricing

        Returns:
            ConfigurationRecord or None if not found
        """ 
        response = self.table.get_item(Key={"Configuration": f"{CONFIG_TYPE_CONFIG}#{version}" if version else configuration_type})
        item = response.get("Item")

        if item is None:
            return None

        return ConfigurationRecord.from_dynamodb_item(item)

    def _write_record(self, record: ConfigurationRecord, identifier: Optional[str] = None) -> None:
        """
        Write ConfigurationRecord to DynamoDB using single key.

        Args:
            record: ConfigurationRecord to write
            identifier: Optional identifier for logging (e.g., "v1", "Schema")
        """
        item = record.to_dynamodb_item()
        self.table.put_item(Item=item)
        
        # Generate log identifier
        if identifier:
            log_id = identifier
        elif record.configuration_type == CONFIG_TYPE_CONFIG and record.version:
            log_id = f"{CONFIG_TYPE_CONFIG}#{record.version}"
        else:
            log_id = record.configuration_type
            
        logger.info(f"Saved configuration: {log_id}")
