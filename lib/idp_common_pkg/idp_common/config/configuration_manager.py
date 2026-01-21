# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import boto3
import json
import os
from typing import Dict, Any, Optional, Union
from botocore.exceptions import ClientError
import logging

from .models import IDPConfig, SchemaConfig, PricingConfig, ConfigurationRecord
from .merge_utils import deep_update, get_diff_dict
from .constants import (
    CONFIG_TYPE_SCHEMA,
    CONFIG_TYPE_DEFAULT,
    CONFIG_TYPE_CUSTOM,
    CONFIG_TYPE_DEFAULT_PRICING,
    CONFIG_TYPE_CUSTOM_PRICING,
    VALID_CONFIG_TYPES,
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
        self.table = self.dynamodb.Table(table_name)  # pyright: ignore[reportAttributeAccessIssue]
        self.table_name = table_name
        logger.info(f"ConfigurationManager initialized with table: {table_name}")

    def get_configuration(
        self, config_type: str
    ) -> Optional[Union[SchemaConfig, IDPConfig, PricingConfig]]:
        """
        Retrieve configuration from DynamoDB.

        This method:
        1. Reads the DynamoDB item
        2. Deserializes into ConfigurationRecord (auto-migrates legacy format)
        3. Checks if migration occurred and persists if needed
        4. Returns SchemaConfig for Schema type, PricingConfig for Pricing, IDPConfig for Default/Custom

        Args:
            config_type: Configuration type (Schema, Default, Custom, Pricing)

        Returns:
            SchemaConfig for Schema type, PricingConfig for Pricing, IDPConfig for Default/Custom, or None if not found

        Raises:
            ClientError: If DynamoDB operation fails
        """
        try:
            record = self._read_record(config_type)
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
        skip_sync: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Save configuration to DynamoDB.

        This method:
        1. Converts dict to appropriate config type if needed
        2. If saving Default, syncs Custom to preserve user customizations (unless skip_sync=True)
        3. Creates ConfigurationRecord
        4. Serializes to DynamoDB item
        5. Writes to DynamoDB
        
        Args:
            config_type: Configuration type (Schema, Default, Custom, DefaultPricing, CustomPricing)
            config: SchemaConfig, IDPConfig, PricingConfig model, or dict (dict will be converted to appropriate type)
            skip_sync: If True, skip automatic Custom sync when saving Default (used for save-as-default)

        Raises:
            ClientError: If DynamoDB operation fails
        """
        # Convert dict to appropriate config type if needed (for backward compatibility)
        if isinstance(config, dict):
            if config_type == CONFIG_TYPE_SCHEMA:
                config = SchemaConfig(**config)
            elif config_type in (CONFIG_TYPE_DEFAULT_PRICING, CONFIG_TYPE_CUSTOM_PRICING):
                config = PricingConfig(**config)
            else:
                config = IDPConfig(**config)

        # If updating Default, sync Custom to preserve user customizations
        # Skip sync if this is a "save as default" operation where Custom will be deleted
        if (
            config_type == CONFIG_TYPE_DEFAULT
            and not skip_sync
            and isinstance(config, IDPConfig)
        ):
            old_default = self.get_configuration(CONFIG_TYPE_DEFAULT)
            old_custom = self.get_configuration(CONFIG_TYPE_CUSTOM)

            if (
                old_default
                and old_custom
                and isinstance(old_default, IDPConfig)
                and isinstance(old_custom, IDPConfig)
            ):
                logger.info(
                    "Syncing Custom config with new Default while preserving user customizations"
                )
                new_custom = self.sync_custom_with_new_default(
                    old_default, config, old_custom
                )
                # Save the synced custom config
                self.save_configuration(CONFIG_TYPE_CUSTOM, new_custom, skip_sync=True)

        # Create record with metadata
        from .models import ConfigMetadata
        
        config_metadata = None
        if metadata:
            config_metadata = ConfigMetadata(**metadata)
        
        # Map configuration type for discriminated union
        if config_type == CONFIG_TYPE_SCHEMA:
            discriminator_type = "Schema"
        elif config_type in (CONFIG_TYPE_DEFAULT_PRICING, CONFIG_TYPE_CUSTOM_PRICING):
            discriminator_type = config_type  # "DefaultPricing" or "CustomPricing"
        else:
            # All version strings (v0, v1, etc.) use "Config" for discrimination
            discriminator_type = "Config"
            
        record = ConfigurationRecord(
            configuration_type=discriminator_type, 
            config=config,
            metadata=config_metadata
        )

        # Write to DynamoDB with original config_type as key
        self._write_record(record, config_type)

        

    def delete_configuration(self, config_type: str) -> None:
        """
        Delete configuration from DynamoDB.

        Args:
            config_type: Configuration type to delete

        Raises:
            ClientError: If DynamoDB operation fails
        """
        try:
            self.table.delete_item(Key={"Configuration": config_type})
            logger.info(f"Deleted configuration: {config_type}")
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

    def save_custom_pricing(self, pricing_deltas: Union[PricingConfig, Dict[str, Any]]) -> bool:
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
        self, custom_config: Union[str, Dict[str, Any], IDPConfig]
    ) -> bool:
        """
        Handle the updateConfiguration GraphQL mutation.

        This method:
        1. Parses the input (JSON string, dict, or IDPConfig)
        2. Validates that config is not empty (prevents data loss)
        3. Checks for saveAsDefault flag
        4. Either updates Custom or merges into Default
        
        Args:
            custom_config: Configuration as JSON string, dict, or IDPConfig

        Returns:
            True on success

        Raises:
            Exception: If configuration update fails or is empty
        """
        # Reject completely empty configuration to prevent accidental data deletion
        if not custom_config:
            logger.error("Rejecting empty configuration update")
            raise Exception(
                "Cannot update with empty configuration. Frontend should not send empty diffs."
            )

        # Parse input
        if isinstance(custom_config, str):
            config_dict = json.loads(custom_config)
        elif isinstance(custom_config, IDPConfig):
            config_dict = custom_config.model_dump(mode="python")
        else:
            config_dict = custom_config

        # Additional validation: reject if parsed config is empty dict
        if isinstance(config_dict, dict) and len(config_dict) == 0:
            logger.error("Rejecting empty configuration dict")
            raise Exception(
                "Cannot update with empty configuration. Frontend should not send empty diffs."
            )

        # Extract special flags
        save_as_default = config_dict.pop("saveAsDefault", False)
        reset_to_default = config_dict.pop("resetToDefault", False)
        
        # Remove legacy pricing field if present (now stored separately as DefaultPricing/CustomPricing)
        # This handles imported configs that may have old embedded pricing
        config_dict.pop("pricing", None)

        # Handle reset to default - delete Custom entirely
        # On next getConfiguration, the auto-copy logic will repopulate Custom from Default
        if reset_to_default:
            logger.info("Resetting Custom configuration by deleting it")
            self.delete_configuration(CONFIG_TYPE_CUSTOM)
            logger.info(
                "Custom configuration deleted, will be repopulated from Default on next read"
            )
            return True

        # Convert to IDPConfig
        config = IDPConfig(**config_dict)

        if save_as_default:
            # Save as Default: Replace Default with the received config (current Custom state)
            # Frontend sends the complete merged Custom config
            # This becomes the new baseline for all users
            # Skip sync since we're about to delete Custom anyway
            self.save_configuration(CONFIG_TYPE_DEFAULT, config, skip_sync=True)

            # Delete Custom since it's now the same as Default
            self.delete_configuration(CONFIG_TYPE_CUSTOM)

            logger.info("Saved current Custom as new Default and cleared Custom")
        else:
            # Normal custom config update - merge diff into existing Custom
            # Data Flow: Frontend sends diff, we merge into existing Custom
            # Note: Custom should always exist (getConfiguration copies Default on first read)
            existing_custom = self.get_configuration(CONFIG_TYPE_CUSTOM)
            if not existing_custom or not existing_custom.model_dump(
                exclude_unset=True
            ):
                # Fallback: If Custom is somehow empty, use Default as base
                # This should rarely happen due to auto-copy in getConfiguration
                logger.warning(
                    "Custom config is empty during update, using Default as base"
                )
                existing_custom = (
                    self.get_configuration(CONFIG_TYPE_DEFAULT) or IDPConfig()
                )

            # Apply the diff to existing Custom (deep update to handle nested objects)
            existing_dict = existing_custom.model_dump(mode="python")
            update_dict = config.model_dump(mode="python", exclude_unset=True)
            deep_update(existing_dict, update_dict)
            merged_custom = IDPConfig(**existing_dict)

            # Save updated Custom configuration
            self.save_configuration(CONFIG_TYPE_CUSTOM, merged_custom)
            logger.info("Updated Custom configuration by merging diff")

        return True

    # ===== Private Methods =====

    def _read_record(self, config_type: str) -> Optional[ConfigurationRecord]:
        """
        Read ConfigurationRecord from DynamoDB.

        Args:
            config_type: Configuration type to read

        Returns:
            ConfigurationRecord or None if not found
        """
        response = self.table.get_item(Key={"Configuration": config_type})
        item = response.get("Item")

        if item is None:
            return None

        return ConfigurationRecord.from_dynamodb_item(item)

    def _write_record(self, record: ConfigurationRecord, key: Optional[str] = None) -> None:
        """
        Write ConfigurationRecord to DynamoDB.

        Args:
            record: ConfigurationRecord to write
            key: Optional custom key to use instead of record.configuration_type
        """
        item = record.to_dynamodb_item()
        if key:
            item["Configuration"] = key
        self.table.put_item(Item=item)
        logger.info(f"Saved configuration: {key or record.configuration_type}")
