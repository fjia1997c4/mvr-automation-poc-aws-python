// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import React, { useState, useEffect, useRef, useMemo } from 'react';
import PropTypes from 'prop-types';
import { useLocation } from 'react-router-dom';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Alert,
  Spinner,
  Form,
  SegmentedControl,
  Modal,
  FormField,
  Input,
  RadioGroup,
  Icon,
  Badge,
  Table,
  ExpandableSection,
  CodeEditor,
} from '@cloudscape-design/components';
import Editor from '@monaco-editor/react';
// eslint-disable-next-line import/no-extraneous-dependencies
import yaml from 'js-yaml';
// eslint-disable-next-line import/no-extraneous-dependencies
import ace from 'ace-builds';
import ReactMarkdown from 'react-markdown';
import { generateClient } from 'aws-amplify/api';
import { ConsoleLogger } from 'aws-amplify/utils';
import useConfigurationVersions from '../../hooks/use-configuration-versions';
import useConfigurationLibrary from '../../hooks/use-configuration-library';
import useSettingsContext from '../../contexts/settings';
import ConfigBuilder from './ConfigBuilder';
import ConfigurationVersionsTable from './ConfigurationVersionsTable';
import { deepMerge } from '../../utils/configUtils';
import syncBdaIdpMutation from '../../graphql/queries/syncBdaIdp';
import getConfigVersionQuery from '../../graphql/queries/getConfigVersion';

const client = generateClient();
const logger = new ConsoleLogger('ConfigurationLayout');

// Compare Versions Content Component
const CompareVersionsContent = ({ selectedVersions, versions }) => {
  const [versionData, setVersionData] = useState({});
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState('differences');

  useEffect(() => {
    const fetchVersionsData = async () => {
      setLoading(true);
      const data = {};

      try {
        for (const versionId of selectedVersions) {
          const result = await client.graphql({
            query: getConfigVersionQuery,
            variables: { versionId },
          });

          if (result.data.getConfigVersion.success) {
            const versionInfo = versions.find((v) => v.versionId === versionId);
            data[versionId] = {
              configuration: JSON.parse(result.data.getConfigVersion.Configuration || '{}'),
              isActive: versionInfo?.isActive || false,
            };
          }
        }
        setVersionData(data);
      } catch (error) {
        logger.error('Error fetching versions:', error);
        // Set empty data to prevent blank page
        setVersionData({});
      } finally {
        setLoading(false);
      }
    };

    if (selectedVersions.length > 0) {
      fetchVersionsData();
    } else {
      setLoading(false);
    }
  }, [selectedVersions, versions]);

  const formatContent = (config, format) => {
    try {
      if (format === 'yaml') {
        return yaml.dump(config, { indent: 2, lineWidth: -1 });
      }
      return JSON.stringify(config, null, 2);
    } catch (err) {
      return `Error formatting: ${err.message}`;
    }
  };

  // Function to find differences between configurations
  const findDifferences = () => {
    const differences = [];
    const allPaths = new Set();

    // Get all possible paths from all versions
    const getAllPaths = (obj, prefix = '') => {
      Object.keys(obj || {}).forEach((key) => {
        const path = prefix ? `${prefix}.${key}` : key;
        allPaths.add(path);
        if (typeof obj[key] === 'object' && obj[key] !== null && !Array.isArray(obj[key])) {
          getAllPaths(obj[key], path);
        }
      });
    };

    selectedVersions.forEach((versionId) => {
      getAllPaths(versionData[versionId]?.configuration);
    });

    // Filter out parent paths if child paths exist
    const filteredPaths = Array.from(allPaths).filter((path) => {
      // Check if any other path starts with this path + "."
      return !Array.from(allPaths).some((otherPath) => otherPath !== path && otherPath.startsWith(path + '.'));
    });

    // Check each filtered path for differences
    filteredPaths.forEach((path) => {
      const values = {};

      selectedVersions.forEach((versionId) => {
        const value = getValueByPath(versionData[versionId]?.configuration, path);
        values[versionId] = value;
      });

      // Check if values are different
      const uniqueValues = new Set(Object.values(values).map((v) => JSON.stringify(v)));
      if (uniqueValues.size > 1) {
        differences.push({ path, values });
      }
    });

    return differences;
  };

  // Helper function to get value by dot notation path
  const getValueByPath = (obj, path) => {
    return path.split('.').reduce((current, key) => {
      return current && current[key] !== undefined ? current[key] : undefined;
    }, obj);
  };

  const renderDifferencesTable = () => {
    const differences = findDifferences();

    if (differences.length === 0) {
      return (
        <Box textAlign="center" padding="l">
          <Box variant="h3">No differences found</Box>
          <Box>All selected versions have identical configurations.</Box>
        </Box>
      );
    }

    const totalColumns = selectedVersions.length + 1; // +1 for field path column
    const columnWidth = Math.floor(100 / totalColumns);

    const columnDefinitions = [
      {
        id: 'field',
        header: 'Field Path',
        cell: (item) => <Box variant="code">{item.path}</Box>,
        sortingField: 'path',
        isRowHeader: true,
        width: `${columnWidth}%`,
      },
      ...selectedVersions.map((versionId) => ({
        id: versionId,
        header: `${versionId} ${versionData[versionId]?.isActive ? '(Active)' : ''}`,
        cell: (item) => {
          const value = item.values[versionId];
          return (
            <Box variant="code" fontSize="body-s">
              {value === undefined ? '(undefined)' : JSON.stringify(value)}
            </Box>
          );
        },
        width: `${columnWidth}%`,
      })),
    ];

    return (
      <Table
        columnDefinitions={columnDefinitions}
        items={differences}
        resizableColumns
        variant="borderless"
        header={<Header variant="h3">Config Differences ({differences.length} fields)</Header>}
        empty={
          <Box margin={{ vertical: 'xs' }} textAlign="center" color="inherit">
            <SpaceBetween size="m">
              <b>No differences found</b>
              <Box variant="p" color="inherit">
                All selected versions have identical configurations.
              </Box>
            </SpaceBetween>
          </Box>
        }
      />
    );
  };

  if (loading) {
    return (
      <Box textAlign="center" padding="l">
        <Spinner size="large" />
        <Box padding="s">Loading versions...</Box>
      </Box>
    );
  }

  return <SpaceBetween size="m">{renderDifferencesTable()}</SpaceBetween>;
};

CompareVersionsContent.propTypes = {
  selectedVersions: PropTypes.arrayOf(PropTypes.string).isRequired,
  versions: PropTypes.arrayOf(
    PropTypes.shape({
      versionId: PropTypes.string.isRequired,
      isActive: PropTypes.bool,
    }),
  ).isRequired,
};

// Utility function to check if two values are numerically equivalent
const areNumericValuesEqual = (val1, val2) => {
  if (typeof val1 === 'number' && typeof val2 === 'number') {
    return val1 === val2;
  }
  const num1 = typeof val1 === 'number' ? val1 : parseFloat(val1);
  const num2 = typeof val2 === 'number' ? val2 : parseFloat(val2);
  if (!Number.isNaN(num1) && !Number.isNaN(num2)) {
    return num1 === num2;
  }
  return false;
};

const isNumericValue = (val) => {
  if (typeof val === 'number') return true;
  if (typeof val === 'string' && val.trim() !== '') {
    return !Number.isNaN(parseFloat(val)) && isFinite(val);
  }
  return false;
};

const ConfigurationLayout = () => {
  const {
    versions,
    loading: versionsLoading,
    fetchVersions,
    fetchVersion,
    saveAsNewVersion,
    setActiveVersion,
    updateVersion,
    deleteVersion,
  } = useConfigurationVersions();

  // URL parameter handling
  const location = useLocation();

  // Version selection state
  const [selectedVersion, setSelectedVersion] = useState(null);
  const [selectedVersionData, setSelectedVersionData] = useState(null);
  const [defaultVersionData, setDefaultVersionData] = useState(null); // v0 for comparison
  const [loadingVersion, setLoadingVersion] = useState(false);

  // Compare versions state
  const [selectedVersionsForCompare, setSelectedVersionsForCompare] = useState([]);
  const [showCompareModal, setShowCompareModal] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [versionsToDelete, setVersionsToDelete] = useState([]);

  // Configuration editing state (from develop branch)
  const [formValues, setFormValues] = useState({});
  const [jsonContent, setJsonContent] = useState('');
  const [yamlContent, setYamlContent] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [importSuccess, setImportSuccess] = useState(false);
  const [validationErrors, setValidationErrors] = useState([]);
  const [viewMode, setViewMode] = useState('form');
  const [showResetModal, setShowResetModal] = useState(false);
  const [showSaveAsNewModal, setShowSaveAsNewModal] = useState(false);
  const [showVersionConfirmationModal, setShowVersionConfirmationModal] = useState(false);
  const [confirmationModalType, setConfirmationModalType] = useState(''); // 'save' or 'import'
  const [newlyCreatedVersionId, setNewlyCreatedVersionId] = useState('');
  const [showExportModal, setShowExportModal] = useState(false);
  const [showActivateModal, setShowActivateModal] = useState(false);
  const [showActivateConfirmationModal, setShowActivateConfirmationModal] = useState(false);
  const [activatedVersionId, setActivatedVersionId] = useState('');
  const [versionToActivate, setVersionToActivate] = useState('');
  const [showBulkDeleteModal, setShowBulkDeleteModal] = useState(false);
  const [showBulkDeleteSuccessModal, setShowBulkDeleteSuccessModal] = useState(false);
  const [deletedVersionsDisplay, setDeletedVersionsDisplay] = useState('');
  const [exportFormat, setExportFormat] = useState('json');
  const [exportFileName, setExportFileName] = useState('configuration');
  const [importError, setImportError] = useState(null);
  const [extractionSchema, setExtractionSchema] = useState(null);
  const [ruleSchema, setRuleSchema] = useState(null);
  const [newVersionDescription, setNewVersionDescription] = useState('');

  // Configuration Library state
  const [showImportSourceModal, setShowImportSourceModal] = useState(false);
  const [showImportAsNewVersionModal, setShowImportAsNewVersionModal] = useState(false);
  const [importedConfigForNewVersion, setImportedConfigForNewVersion] = useState(null);
  const [showLibraryBrowserModal, setShowLibraryBrowserModal] = useState(false);
  const [libraryImportContext, setLibraryImportContext] = useState('current'); // 'current' or 'new'
  const [showReadmeModal, setShowReadmeModal] = useState(false);
  const [libraryConfigs, setLibraryConfigs] = useState([]);
  const [selectedLibraryConfig, setSelectedLibraryConfig] = useState(null);
  const [readmeContent, setReadmeContent] = useState('');
  const [libraryLoading, setLibraryLoading] = useState(false);

  const editorRef = useRef(null);
  const { listConfigurations, getFile } = useConfigurationLibrary();
  const { settings } = useSettingsContext();

  // BDA/IDP Sync state
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncSuccess, setSyncSuccess] = useState(false);
  const [syncSuccessMessage, setSyncSuccessMessage] = useState('');
  const [syncError, setSyncError] = useState(null);

  // Helper function to check if Pattern-1 is selected
  const isPattern1 = settings?.IDPPattern?.includes('Pattern1');

  // Helper function to check if Pattern-2 is selected (for Rule Schema feature)
  const isPattern2 = settings?.IDPPattern?.includes('Pattern2');

  // Validate the current content based on view mode
  const validateCurrentContent = () => {
    try {
      if (viewMode === 'json') {
        JSON.parse(jsonContent);
        return [];
      }
      if (viewMode === 'yaml') {
        yaml.load(yamlContent);
        return [];
      }
      return [];
    } catch (e) {
      return [{ message: `Invalid ${viewMode.toUpperCase()}: ${e.message}` }];
    }
  };

  // Format JSON in editor
  const formatJson = () => {
    if (editorRef.current && viewMode === 'json') {
      editorRef.current.getAction('editor.action.formatDocument').run();
    }
  };

  // Format YAML in editor
  const formatYaml = () => {
    if (editorRef.current && viewMode === 'yaml') {
      editorRef.current.getAction('editor.action.formatDocument').run();
    }
  };

  // Handle form value changes and sync with editors
  const handleFormChange = (newValues) => {
    setFormValues(newValues);
    try {
      const jsonString = JSON.stringify(newValues, null, 2);
      setJsonContent(jsonString);
      const yamlString = yaml.dump(newValues);
      setYamlContent(yamlString);
    } catch (e) {
      console.error('Error converting form values:', e);
    }
  };

  // Create merged config (same pattern as develop branch)
  const mergedConfig = useMemo(() => {
    if (!selectedVersionData) return null;

    if (selectedVersion === 'v0' || !defaultVersionData) {
      // For v0 or when v0 isn't loaded, use the selected version directly
      return selectedVersionData.configuration;
    } else {
      // Merge v0 (defaults) with selected version (overrides) using deepMerge
      return deepMerge(defaultVersionData.configuration, selectedVersionData.configuration);
    }
  }, [selectedVersionData, defaultVersionData, selectedVersion]);

  // Check if current version has unsaved changes (same as develop branch)
  const hasUnsavedChanges = useMemo(() => {
    if (!mergedConfig || !formValues || Object.keys(formValues).length === 0) {
      return false;
    }
    // Deep comparison using JSON serialization
    return JSON.stringify(formValues) !== JSON.stringify(mergedConfig);
  }, [formValues, mergedConfig]);

  // Handler for BDA/IDP sync
  const handleSyncBdaIdp = async () => {
    setIsSyncing(true);
    setSyncSuccess(false);
    setSyncSuccessMessage('');
    setSyncError(null);

    try {
      logger.debug('Starting BDA/IDP sync...');

      const result = await client.graphql({
        query: syncBdaIdpMutation,
      });

      logger.debug('Sync API response:', result);

      const response = result.data.syncBdaIdp;

      if (response.success) {
        setSyncSuccess(true);
        setSyncSuccessMessage(response.message || 'Document classes have been synchronized with BDA blueprints.');

        // If there are partial failures, also show the error details
        if (response.error && response.error.type === 'PARTIAL_SYNC_ERROR') {
          setTimeout(() => {
            setSyncError(response.error.message);
          }, 100);
        }

        // Refresh current version to show any new classes
        if (selectedVersion) {
          await handleVersionSelect(selectedVersion);
        }
        setTimeout(() => {
          setSyncSuccess(false);
          setSyncSuccessMessage('');
        }, 5000);
        logger.debug('BDA/IDP sync completed successfully');
      } else {
        const errorMsg = response.error?.message || response.message || 'Sync operation failed';
        setSyncError(errorMsg);
        logger.error('Sync failed:', errorMsg);
      }
    } catch (err) {
      logger.error('Sync error:', err);
      setSyncError(`Sync failed: ${err.message}`);
    } finally {
      setIsSyncing(false);
    }
  };

  // Handle version selection
  const handleVersionSelect = async (versionId) => {
    try {
      setLoadingVersion(true);
      setSaveError(null); // Clear any previous errors
      console.log('Loading version:', versionId);

      const versionData = await fetchVersion(versionId);
      console.log('Version data received:', versionData);

      if (versionData && versionData.configuration) {
        let config;
        if (typeof versionData.configuration === 'string') {
          try {
            config = JSON.parse(versionData.configuration);
          } catch (parseError) {
            console.error('Error parsing configuration JSON:', parseError);
            setSaveError('Invalid configuration data format');
            setLoadingVersion(false);
            return;
          }
        } else {
          config = versionData.configuration;
        }

        console.log('Parsed config:', config);

        setSelectedVersion(versionId);
        // Get the version data with isActive status from the versions list
        const versionFromList = versions.find((v) => v.versionId === versionId);
        const versionDataWithStatus = { ...versionData, isActive: versionFromList?.isActive };
        setSelectedVersionData(versionDataWithStatus);
        setFormValues(config);

        // Reset any unsaved changes indicators when switching versions
        setSaveSuccess(false);
        setSaveError(null);

        // Load v0 for comparison if not already loaded and not selecting v0
        if (versionId !== 'v0' && !defaultVersionData) {
          try {
            const v0Data = await fetchVersion('v0');
            if (v0Data && v0Data.configuration) {
              let v0Config;
              if (typeof v0Data.configuration === 'string') {
                v0Config = JSON.parse(v0Data.configuration);
              } else {
                v0Config = v0Data.configuration;
              }
              setDefaultVersionData({ ...v0Data, configuration: v0Config });
            }
          } catch (error) {
            console.warn('Could not load v0 for comparison:', error);
          }
        }

        console.log('State set - selectedVersion:', versionId);
        console.log('State set - formValues:', config);

        if (config.classes) {
          console.log('Setting extractionSchema with classes:', config.classes);
          setExtractionSchema(config.classes);
        } else {
          console.log('No classes found in config, setting extractionSchema to empty array');
          setExtractionSchema([]);
        }

        // Initialize rule schema from config (stored in rule_classes field)
        if (config.rule_classes) {
          setRuleSchema(config.rule_classes);
        } else {
          setRuleSchema([]);
        }

        // Update editor content
        const jsonString = JSON.stringify(config, null, 2);
        setJsonContent(jsonString);

        try {
          const yamlString = yaml.dump(config);
          setYamlContent(yamlString);
        } catch (e) {
          console.error('Error converting to YAML:', e);
          setYamlContent('# Error converting to YAML');
        }
      } else {
        console.error('No configuration data in version response:', versionData);
        setSaveError('No configuration data found for this version');
      }
    } catch (err) {
      console.error('Error loading version:', err);
      setSaveError(`Failed to load version: ${err.message}`);
    } finally {
      setLoadingVersion(false);
    }
  };

  // Handle URL parameters to auto-select version
  useEffect(() => {
    // Handle hash-based routing - extract search params from hash
    const hash = window.location.hash;
    const searchParamsMatch = hash.match(/\?(.+)$/);

    if (searchParamsMatch) {
      const searchParams = new URLSearchParams(searchParamsMatch[1]);
      const versionParam = searchParams.get('version');

      if (versionParam && versions.length > 0) {
        // Check if the version exists in the versions list
        const versionExists = versions.find((v) => v.versionId === versionParam);
        if (versionExists) {
          console.log('Auto-selecting version from URL:', versionParam);
          // Auto-select the version from URL parameter
          handleVersionSelect(versionParam);
        }
      }
    }
  }, [versions]);

  // Handle back to versions list (now just clears selection)
  const handleBackToVersions = () => {
    setSelectedVersion(null);
    setSelectedVersionData(null);
    setFormValues({});
    setJsonContent('');
    setYamlContent('');
    setExtractionSchema(null);
    setRuleSchema(null);
    setSaveError(null);
    setSaveSuccess(false);
  };

  // Handle bulk activate version
  const handleBulkActivateVersion = async (versionId) => {
    setVersionToActivate(versionId);
    setShowActivateModal(true);
  };

  // Confirm activate version
  const confirmActivateVersion = async () => {
    try {
      await setActiveVersion(versionToActivate);
      setSaveSuccess(true);
      setShowActivateModal(false);
      // Show success confirmation dialog
      setShowActivateConfirmationModal(true);
      // Find version description and format display
      const versionData = versions.find((v) => v.versionId === versionToActivate);
      const versionDisplay = versionData?.description ? `${versionToActivate} (${versionData.description})` : versionToActivate;
      setActivatedVersionId(versionDisplay);
      await fetchVersions();
      // Clear selection after activation
      setSelectedVersionsForCompare([]);
    } catch (error) {
      console.error('Activate error:', error);
      setSaveError(`Failed to activate version: ${error.message}`);
      setShowActivateModal(false);
    }
  };

  // Handle import as new version
  const handleImportAsNewVersion = () => {
    setShowImportAsNewVersionModal(true);
  };

  // Handle file import for new version
  const handleImportFileForNewVersion = async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        const content = e.target.result;
        let config;

        if (file.name.endsWith('.yaml') || file.name.endsWith('.yml')) {
          config = yaml.load(content);
        } else {
          config = JSON.parse(content);
        }

        // Ensure v0 is loaded for merging
        let v0Data = defaultVersionData;
        if (!v0Data) {
          try {
            const v0Response = await fetchVersion('v0');
            if (v0Response && v0Response.configuration) {
              let v0Config;
              if (typeof v0Response.configuration === 'string') {
                v0Config = JSON.parse(v0Response.configuration);
              } else {
                v0Config = v0Response.configuration;
              }
              v0Data = { ...v0Response, configuration: v0Config };
              setDefaultVersionData(v0Data);
            }
          } catch (error) {
            console.warn('Could not load v0 for merging:', error);
          }
        }

        // Merge with v0 defaults to fill missing fields
        let configToImport = config;
        if (v0Data && v0Data.configuration) {
          configToImport = {
            ...v0Data.configuration,
            ...config,
          };
        }

        setImportedConfigForNewVersion(configToImport);
        // Set filename as default description
        const fileName = file.name.replace(/\.(json|yaml|yml)$/i, '');
        setNewVersionDescription(fileName);
      } catch (error) {
        setSaveError(`Failed to parse ${file.name}: ${error.message}`);
      }
    };
    reader.readAsText(file);
    event.target.value = ''; // Reset file input
  };

  // Handle creating new version from imported config
  const handleCreateVersionFromImport = async () => {
    if (!importedConfigForNewVersion) {
      setSaveError('Please import a configuration file');
      return;
    }

    setIsSaving(true);
    setSaveError(null);

    try {
      const description = newVersionDescription.trim() || 'New imported version';
      const result = await saveAsNewVersion(importedConfigForNewVersion, description);
      setShowImportAsNewVersionModal(false);
      setImportedConfigForNewVersion(null);
      setNewVersionDescription('');
      setSaveSuccess(true);
      // Show confirmation dialog
      setShowVersionConfirmationModal(true);
      setConfirmationModalType('import');
      // Store the new version ID and description for the confirmation dialog
      setNewlyCreatedVersionId(`${result?.versionId || 'Unknown'} (${description})`);
      // Refresh versions list
      await fetchVersions();
    } catch (error) {
      console.error('Create version error:', error);
      setSaveError(error.message || 'Failed to create version from imported configuration');
    } finally {
      setIsSaving(false);
    }
  };

  // Handle bulk delete versions - show confirmation dialog
  const handleBulkDeleteVersions = async (versionIds) => {
    setVersionsToDelete(versionIds);
    setShowBulkDeleteModal(true);
  };

  // Confirm delete versions
  const confirmDeleteVersions = async () => {
    try {
      // Format deleted versions with descriptions for display
      const deletedVersionsWithDesc = versionsToDelete.map((versionId) => {
        const versionData = versions.find((v) => v.versionId === versionId);
        return versionData?.description ? `${versionId} (${versionData.description})` : versionId;
      });

      for (const versionId of versionsToDelete) {
        await deleteVersion(versionId);
      }

      // If the currently selected version was deleted, clear it
      if (selectedVersion && versionsToDelete.includes(selectedVersion)) {
        setSelectedVersion(null);
        setSelectedVersionData(null);
        setFormValues({});
        setJsonContent('');
        setYamlContent('');
      }

      // Show success confirmation
      setDeletedVersionsDisplay(deletedVersionsWithDesc.join(', '));
      setShowBulkDeleteSuccessModal(true);
    } catch (error) {
      console.error('Delete error:', error);
      setSaveError(`Failed to delete versions: ${error.message}`);
    } finally {
      setShowBulkDeleteModal(false);
      setVersionsToDelete([]);
      // Clear selection after deletion
      setSelectedVersionsForCompare([]);
    }
  };

  // Handle version comparison
  const handleCompareVersions = () => {
    if (selectedVersionsForCompare.length < 2) {
      return;
    }
    setShowCompareModal(true);
  };

  // Handle version selection for comparison
  const handleVersionSelectForCompare = (versionId, selected) => {
    if (selected) {
      setSelectedVersionsForCompare((prev) => [...prev, versionId]);
    } else {
      setSelectedVersionsForCompare((prev) => prev.filter((v) => v !== versionId));
    }
  };

  // Handle save current version
  const handleSave = async () => {
    // Validate content before saving
    const currentErrors = validateCurrentContent();
    if (currentErrors.length > 0) {
      setValidationErrors(currentErrors);
      setSaveError('Cannot save: Configuration contains validation errors');
      return;
    }

    setIsSaving(true);
    setSaveError(null);
    setSaveSuccess(false);

    try {
      await updateVersion(selectedVersion, formValues);
      setSaveSuccess(true);
    } catch (error) {
      console.error('Save error:', error);
      setSaveError(error.message || 'Failed to save configuration');
    } finally {
      setIsSaving(false);
    }
  };

  // Handle save as new version
  const handleSaveAsNew = async () => {
    setIsSaving(true);
    setSaveError(null);
    setSaveSuccess(false);

    try {
      const result = await saveAsNewVersion(formValues, newVersionDescription || `New version based on ${selectedVersion}`);
      setSaveSuccess(true);
      setShowSaveAsNewModal(false);
      setNewVersionDescription('');
      // Show confirmation dialog
      setShowVersionConfirmationModal(true);
      setConfirmationModalType('save');
      // Store the new version ID and description for the confirmation dialog
      setNewlyCreatedVersionId(`${result?.versionId || 'Unknown'} (${newVersionDescription || `New version based on ${selectedVersion}`})`);
      // Refresh versions list
      await fetchVersions();
    } catch (error) {
      console.error('Save as new error:', error);
      setSaveError(error.message || 'Failed to save as new version');
    } finally {
      setIsSaving(false);
    }
  };

  // Handle activate version
  const handleActivateVersion = async () => {
    setIsSaving(true);
    setSaveError(null);

    try {
      await setActiveVersion(selectedVersion);
      setSaveSuccess(true);
      setShowActivateModal(false);
      // Show confirmation dialog
      setShowActivateConfirmationModal(true);
      // Find version description and format display
      const versionData = versions.find((v) => v.versionId === selectedVersion);
      const versionDisplay = versionData?.description ? `${selectedVersion} (${versionData.description})` : selectedVersion;
      setActivatedVersionId(versionDisplay);
      // Refresh versions list to update active status
      await fetchVersions();
      // Update version data to reflect active status
      setSelectedVersionData((prev) => ({ ...prev, isActive: true }));
    } catch (error) {
      console.error('Activate error:', error);
      setSaveError(error.message || 'Failed to activate version');
    } finally {
      setIsSaving(false);
    }
  };

  // Handle delete version
  const handleDeleteVersion = async () => {
    setIsSaving(true);
    setSaveError(null);

    try {
      await deleteVersion(selectedVersion);
      setShowDeleteModal(false);
      setSaveSuccess(true);
      // Clear the selected version since it was deleted
      setSelectedVersion(null);
      setSelectedVersionData(null);
    } catch (error) {
      console.error('Delete error:', error);
      setSaveError(error.message || 'Failed to delete version');
    } finally {
      setIsSaving(false);
    }
  };

  // Handle export
  const handleExport = () => {
    try {
      let content;
      let mimeType;
      let fileExtension;

      if (exportFormat === 'yaml') {
        content = yamlContent;
        mimeType = 'text/yaml';
        fileExtension = 'yaml';
      } else {
        content = jsonContent;
        mimeType = 'application/json';
        fileExtension = 'json';
      }

      const blob = new Blob([content], { type: mimeType });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${exportFileName}.${fileExtension}`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      setShowExportModal(false);
    } catch (err) {
      setSaveError(`Export failed: ${err.message}`);
    }
  };

  // Handle import
  const handleImport = async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        setImportError(null);
        const content = e.target.result;

        const importedConfig = file.name.endsWith('.yaml') || file.name.endsWith('.yml') ? yaml.load(content) : JSON.parse(content);

        if (importedConfig && typeof importedConfig === 'object') {
          // Ensure v0 is loaded for merging
          let v0Data = defaultVersionData;
          if (!v0Data) {
            try {
              const v0Response = await fetchVersion('v0');
              if (v0Response && v0Response.configuration) {
                let v0Config;
                if (typeof v0Response.configuration === 'string') {
                  v0Config = JSON.parse(v0Response.configuration);
                } else {
                  v0Config = v0Response.configuration;
                }
                v0Data = { ...v0Response, configuration: v0Config };
                setDefaultVersionData(v0Data);
              }
            } catch (error) {
              console.warn('Could not load v0 for merging:', error);
            }
          }

          // Merge with v0 defaults to fill missing fields
          let configToImport = importedConfig;
          if (v0Data && v0Data.configuration) {
            configToImport = {
              ...v0Data.configuration,
              ...importedConfig,
            };
          }

          setFormValues(configToImport);
          setJsonContent(JSON.stringify(configToImport, null, 2));
          setYamlContent(yaml.dump(configToImport));
          if (configToImport.classes) {
            setExtractionSchema(configToImport.classes);
          }
          if (configToImport.rule_classes) {
            setRuleSchema(configToImport.rule_classes);
          }
          setImportSuccess(true);
        } else {
          setImportError('Invalid configuration file format');
        }
      } catch (err) {
        setImportError(`Import failed: ${err.message}`);
      }
    };
    reader.readAsText(file);
    event.target.value = '';
  };

  // Handler for Import button click
  const handleImportClick = () => {
    setShowImportSourceModal(true);
  };

  // Handler for local file import
  const handleLocalFileImport = () => {
    setShowImportSourceModal(false);
    document.getElementById('import-file').click();
  };

  // Handler for config library import for current version
  const handleConfigLibraryImportForCurrentVersion = async () => {
    setShowImportSourceModal(false);
    setLibraryImportContext('current');
    setLibraryLoading(true);

    try {
      // Determine pattern based on settings
      const pattern = settings?.IDPPattern?.includes('Pattern1')
        ? 'pattern-1'
        : settings?.IDPPattern?.includes('Pattern3')
        ? 'pattern-3'
        : 'pattern-2';

      const configs = await listConfigurations(pattern);
      setLibraryConfigs(configs);
      setShowLibraryBrowserModal(true);
    } catch (error) {
      setSaveError(`Failed to load configuration library: ${error.message}`);
    } finally {
      setLibraryLoading(false);
    }
  };

  // Handler for config library import for new version
  const handleConfigLibraryImport = async () => {
    setLibraryImportContext('new');
    setLibraryLoading(true);

    try {
      // Determine pattern based on settings
      const pattern = settings?.IDPPattern?.includes('Pattern1')
        ? 'pattern-1'
        : settings?.IDPPattern?.includes('Pattern3')
        ? 'pattern-3'
        : 'pattern-2';

      const configs = await listConfigurations(pattern);
      setLibraryConfigs(configs);
      setShowLibraryBrowserModal(true);
    } catch (error) {
      setSaveError(`Failed to load configuration library: ${error.message}`);
    } finally {
      setLibraryLoading(false);
    }
  };

  // Handler for selecting a library configuration for current version
  const handleLibraryConfigSelectForCurrentVersion = async (configName) => {
    setShowLibraryBrowserModal(false);
    setLibraryLoading(true);

    try {
      // Determine pattern based on settings
      const pattern = settings?.IDPPattern?.includes('Pattern1')
        ? 'pattern-1'
        : settings?.IDPPattern?.includes('Pattern3')
        ? 'pattern-3'
        : 'pattern-2';

      const configFile = await getFile(pattern, configName, 'config.yaml');

      if (configFile && configFile.content) {
        // Parse YAML content
        const importedConfig = yaml.load(configFile.content);

        if (importedConfig && typeof importedConfig === 'object') {
          // Ensure v0 is loaded for merging
          let v0Data = defaultVersionData;
          if (!v0Data) {
            try {
              const v0Response = await fetchVersion('v0');
              if (v0Response && v0Response.configuration) {
                let v0Config;
                if (typeof v0Response.configuration === 'string') {
                  v0Config = JSON.parse(v0Response.configuration);
                } else {
                  v0Config = v0Response.configuration;
                }
                v0Data = { ...v0Response, configuration: v0Config };
                setDefaultVersionData(v0Data);
              }
            } catch (error) {
              console.warn('Could not load v0 for merging:', error);
            }
          }

          // Merge with v0 defaults to fill missing fields
          let configToImport = importedConfig;
          if (v0Data && v0Data.configuration) {
            configToImport = {
              ...v0Data.configuration,
              ...importedConfig,
            };
          }

          setFormValues(configToImport);
          setJsonContent(JSON.stringify(configToImport, null, 2));
          setYamlContent(yaml.dump(configToImport));
          if (configToImport.classes) {
            setExtractionSchema(configToImport.classes);
          }
          if (configToImport.rule_classes) {
            setRuleSchema(configToImport.rule_classes);
          }
          setImportSuccess(true);
        } else {
          setImportError('Invalid configuration file format');
        }
      }
    } catch (error) {
      setImportError(`Failed to load configuration: ${error.message}`);
    } finally {
      setLibraryLoading(false);
    }
  };

  // Handler for selecting a library configuration for new version
  const handleLibraryConfigSelect = async (configName) => {
    setLibraryLoading(true);

    try {
      // Determine pattern based on settings
      const pattern = settings?.IDPPattern?.includes('Pattern1')
        ? 'pattern-1'
        : settings?.IDPPattern?.includes('Pattern3')
        ? 'pattern-3'
        : 'pattern-2';

      const configFile = await getFile(pattern, configName, 'config.yaml');

      if (configFile && configFile.content) {
        // Parse YAML content
        const importedConfig = yaml.load(configFile.content);

        if (importedConfig && typeof importedConfig === 'object') {
          // Merge with v0 defaults to fill in missing fields
          let configToImport = importedConfig;
          if (defaultVersionData && defaultVersionData.configuration) {
            configToImport = {
              ...defaultVersionData.configuration,
              ...importedConfig,
            };
          }

          setImportedConfigForNewVersion(configToImport);
          // Use just the base name without path for description
          const baseName = configName.split('/').pop();
          setNewVersionDescription(baseName);
          setShowLibraryBrowserModal(false);
        } else {
          setImportError('Invalid configuration file format');
        }
      }
    } catch (error) {
      setImportError(`Failed to load configuration: ${error.message}`);
    } finally {
      setLibraryLoading(false);
    }
  };

  // Handler for viewing README
  const handleViewReadme = async (configName) => {
    setLibraryLoading(true);

    try {
      // Determine pattern based on settings
      const pattern = settings?.IDPPattern?.includes('Pattern1')
        ? 'pattern-1'
        : settings?.IDPPattern?.includes('Pattern3')
        ? 'pattern-3'
        : 'pattern-2';

      const readmeFile = await getFile(pattern, configName, 'README.md');

      if (readmeFile && readmeFile.content) {
        setReadmeContent(readmeFile.content);
        setSelectedLibraryConfig(configName);
        setShowReadmeModal(true);
      }
    } catch (error) {
      setSaveError(`Failed to load README: ${error.message}`);
    } finally {
      setLibraryLoading(false);
    }
  };

  // Handle reset to default
  // Handle field-level reset to default (for individual field restore buttons)
  const handleFieldResetToDefault = async (fieldPath) => {
    // Can't reset v0 fields
    if (selectedVersion === 'v0' || !defaultVersionData) {
      return;
    }

    try {
      // Get the default value from v0
      const defaultValue = getValueByPath(defaultVersionData.configuration, fieldPath);

      // Update only this field in formValues
      const updatedFormValues = { ...formValues };
      setValueByPath(updatedFormValues, fieldPath, defaultValue);

      // Save the updated configuration to the same version ID
      await updateVersion(selectedVersion, updatedFormValues);

      // Update UI state
      setFormValues(updatedFormValues);
      setJsonContent(JSON.stringify(updatedFormValues, null, 2));
      setYamlContent(yaml.dump(updatedFormValues));
    } catch (error) {
      console.error('Field reset error:', error);
      setSaveError(error.message || 'Failed to reset field to default');
    }
  };

  // Handle full reset to default (All) - resets entire configuration to v0
  const handleResetToDefault = async () => {
    setIsSaving(true);
    setSaveError(null);
    setSaveSuccess(false);

    try {
      // Can't reset v0 to itself
      if (selectedVersion === 'v0') {
        setSaveError('Cannot reset v0 (default) to itself');
        return;
      }

      // Need v0 data to reset to
      if (!defaultVersionData) {
        setSaveError('Default version (v0) not available for reset');
        return;
      }

      // Reset current version to match v0
      const v0Config = defaultVersionData.configuration;

      // Save the v0 configuration to the same version ID
      await updateVersion(selectedVersion, v0Config);

      // Update UI state
      setFormValues(v0Config);
      setJsonContent(JSON.stringify(v0Config, null, 2));
      setYamlContent(yaml.dump(v0Config));

      if (v0Config.classes) {
        setExtractionSchema(v0Config.classes);
      }

      setSaveSuccess(true);
      setShowResetModal(false);
    } catch (error) {
      console.error('Reset error:', error);
      setSaveError(error.message || 'Failed to reset to default');
    } finally {
      setIsSaving(false);
    }
  };

  // Helper function to set value by path
  const setValueByPath = (obj, path, value) => {
    const keys = path.split('.');
    const lastKey = keys.pop();
    const target = keys.reduce((current, key) => {
      if (!current[key]) current[key] = {};
      return current[key];
    }, obj);
    target[lastKey] = value;
  };

  // Helper function to get value by path (e.g., "ocr.backend" -> formValues.ocr.backend)
  const getValueByPath = (obj, path) => {
    return path.split('.').reduce((current, key) => {
      if (current && typeof current === 'object') {
        // Handle array indices like "classes[0].name"
        const arrayMatch = key.match(/^(.+)\[(\d+)\]$/);
        if (arrayMatch) {
          const [, arrayKey, index] = arrayMatch;
          return current[arrayKey] && current[arrayKey][parseInt(index)];
        }
        return current[key];
      }
      return undefined;
    }, obj);
  };

  // Check if a field is customized compared to v0 (default)
  const isCustomized = (path) => {
    // Only show customization for versions other than v0
    if (selectedVersion === 'v0' || !defaultVersionData) {
      return false;
    }

    // Get value from current version and v0
    const currentValue = getValueByPath(formValues, path);
    const defaultValue = getValueByPath(defaultVersionData.configuration, path);

    // Deep comparison
    return JSON.stringify(currentValue) !== JSON.stringify(defaultValue);
  };

  // Show versions table and selected version editor on same page
  return (
    <>
      <SpaceBetween size="l">
        {/* Versions Table */}
        <Container header={<Header variant="h2">Configuration Versions</Header>}>
          <ConfigurationVersionsTable
            versions={versions}
            loading={versionsLoading}
            onVersionSelect={handleVersionSelect}
            selectedVersionsForCompare={selectedVersionsForCompare}
            onVersionSelectForCompare={handleVersionSelectForCompare}
            onCompareVersions={handleCompareVersions}
            onActivateVersion={handleBulkActivateVersion}
            onDeleteVersions={handleBulkDeleteVersions}
            onImportAsNewVersion={handleImportAsNewVersion}
          />
        </Container>

        {/* Loading state for selected version */}
        {loadingVersion && (
          <Container header={<Header variant="h2">Loading Configuration</Header>}>
            <Box textAlign="center" padding="l">
              <Spinner size="large" />
              <Box padding="s">Loading configuration version {selectedVersion}...</Box>
            </Box>
          </Container>
        )}

        {/* Configuration Editor for selected version */}
        {selectedVersion && !loadingVersion && (
          <Container
            header={
              <Header
                variant="h2"
                actions={
                  <SpaceBetween direction="horizontal" size="xs">
                    <SegmentedControl
                      selectedId={viewMode}
                      onChange={({ detail }) => setViewMode(detail.selectedId)}
                      options={[
                        { id: 'form', text: 'Form View' },
                        { id: 'json', text: 'JSON View' },
                        { id: 'yaml', text: 'YAML View' },
                      ]}
                    />
                    {viewMode === 'json' && (
                      <Button onClick={formatJson} iconName="file">
                        Format JSON
                      </Button>
                    )}
                    {viewMode === 'yaml' && (
                      <Button onClick={formatYaml} iconName="file">
                        Format YAML
                      </Button>
                    )}
                    <Button
                      onClick={() => {
                        // Set default filename based on selected version
                        if (selectedVersionData) {
                          const versionId = selectedVersionData.versionId || selectedVersion;
                          // Get description from versions array, not selectedVersionData
                          const versionFromList = versions.find((v) => v.versionId === versionId);
                          const description = versionFromList?.description;
                          let filename = description ? `${versionId}_${description}` : versionId;
                          // Sanitize filename: remove/replace special characters and spaces, clean up multiple underscores
                          filename = filename
                            .replace(/[^a-zA-Z0-9._]/g, '_') // Replace special chars with underscore
                            .replace(/_+/g, '_') // Replace multiple underscores with single
                            .replace(/^_+|_+$/g, ''); // Remove leading/trailing underscores
                          setExportFileName(filename);
                          // Small delay to ensure state updates before modal opens
                          setTimeout(() => setShowExportModal(true), 10);
                        } else {
                          setExportFileName('configuration');
                          setTimeout(() => setShowExportModal(true), 10);
                        }
                      }}
                    >
                      Export
                    </Button>
                    <Button onClick={handleImportClick}>Import</Button>
                    <input id="import-file" type="file" accept=".json,.yaml,.yml" style={{ display: 'none' }} onChange={handleImport} />
                    <Button onClick={() => window.location.reload()}>Refresh</Button>
                    {isPattern1 && (
                      <Button onClick={handleSyncBdaIdp} loading={isSyncing} iconName="refresh">
                        Sync BDA/IDP
                      </Button>
                    )}
                    <Button onClick={() => setShowResetModal(true)} disabled={selectedVersion === 'v0'}>
                      Restore default (All)
                    </Button>
                    <Button
                      onClick={() => {
                        const currentVersionData = versions.find((v) => v.versionId === selectedVersion);
                        const currentDescription = currentVersionData?.description;
                        const defaultDescription = currentDescription ? `${currentDescription} - Copy` : `${selectedVersion} - Copy`;
                        setNewVersionDescription(defaultDescription);
                        setShowSaveAsNewModal(true);
                      }}
                    >
                      Save as Version
                    </Button>
                    <Button
                      variant={hasUnsavedChanges ? 'primary' : 'normal'}
                      onClick={handleSave}
                      loading={isSaving}
                      disabled={!hasUnsavedChanges || selectedVersion === 'v0'}
                    >
                      Save Changes
                    </Button>
                  </SpaceBetween>
                }
              >
                Configuration Version (
                {(() => {
                  const versionFromList = versions.find((v) => v.versionId === selectedVersion);
                  return versionFromList?.description ? `${selectedVersion}) - ${versionFromList.description}` : `${selectedVersion})`;
                })()}
              </Header>
            }
          >
            <SpaceBetween size="l">
              {/* Success/Error alerts */}
              {saveSuccess && (
                <Alert type="success" dismissible onDismiss={() => setSaveSuccess(false)}>
                  Configuration saved successfully!
                </Alert>
              )}
              {syncSuccess && (
                <Alert
                  type="success"
                  dismissible
                  onDismiss={() => {
                    setSyncSuccess(false);
                    setSyncSuccessMessage('');
                  }}
                  header="BDA/IDP sync completed successfully"
                >
                  {syncSuccessMessage}
                </Alert>
              )}
              {saveError && (
                <Alert type="error" dismissible onDismiss={() => setSaveError(null)}>
                  {saveError}
                </Alert>
              )}
              {validationErrors.length > 0 && (
                <Alert type="error" dismissible onDismiss={() => setValidationErrors([])}>
                  <SpaceBetween size="s">
                    <div>Configuration validation errors:</div>
                    <ul style={{ marginTop: '8px', paddingLeft: '20px' }}>
                      {validationErrors.map((error, index) => (
                        <li key={`validation-error-${selectedVersion || 'unknown'}-${index}`} style={{ marginBottom: '4px' }}>
                          {error.message}
                        </li>
                      ))}
                    </ul>
                  </SpaceBetween>
                </Alert>
              )}
              {syncError && (
                <Alert type="error" dismissible onDismiss={() => setSyncError(null)} header="BDA/IDP sync error">
                  <SpaceBetween size="s">
                    {syncError.includes('Failed to sync classes:') ? (
                      <div>
                        <div>The following document classes failed to synchronize:</div>
                        <ul style={{ marginTop: '8px', paddingLeft: '20px' }}>
                          {syncError
                            .replace('Failed to sync classes: ', '')
                            .split(', ')
                            .map((classError) => (
                              <li key={`sync-error-${classError.replace(/[^a-zA-Z0-9]/g, '-')}`} style={{ marginBottom: '4px' }}>
                                {classError}
                              </li>
                            ))}
                        </ul>
                      </div>
                    ) : (
                      <div>{syncError}</div>
                    )}
                  </SpaceBetween>
                </Alert>
              )}

              {/* Configuration content based on view mode */}
              {viewMode === 'form' && (
                <ConfigBuilder
                  schema={{
                    ...JSON.parse(selectedVersionData?.schema || '{}'),
                    properties: Object.fromEntries(
                      Object.entries(JSON.parse(selectedVersionData?.schema || '{}')?.properties || {}).filter(
                        ([key]) => key !== 'classes',
                      ),
                    ),
                  }}
                  formValues={formValues}
                  onChange={handleFormChange}
                  extractionSchema={extractionSchema}
                  isCustomized={isCustomized}
                  onResetToDefault={handleFieldResetToDefault}
                  showRuleSchema={isPattern2}
                  ruleSchema={ruleSchema}
                  onRuleSchemaChange={(schemaData, isDirty) => {
                    setRuleSchema(schemaData);
                    if (isDirty) {
                      const updatedConfig = { ...formValues };
                      // CRITICAL: Always set rule_classes, even if empty array
                      if (schemaData === null) {
                        updatedConfig.rule_classes = [];
                      } else if (Array.isArray(schemaData)) {
                        // Store as 'rule_classes' field with JSON Schema content
                        updatedConfig.rule_classes = schemaData;
                      }
                      setFormValues(updatedConfig);
                    }
                  }}
                  onSchemaChange={(schemaData, isDirty) => {
                    setExtractionSchema(schemaData);
                  }}
                />
              )}

              {viewMode === 'json' && (
                <Editor
                  height="70vh"
                  defaultLanguage="json"
                  value={jsonContent}
                  onChange={(value) => {
                    setJsonContent(value);
                    try {
                      const parsed = JSON.parse(value);
                      setFormValues(parsed);
                      if (parsed.classes) {
                        setExtractionSchema(parsed.classes);
                      }
                      if (parsed.rule_classes) {
                        setRuleSchema(parsed.rule_classes);
                      }
                      try {
                        const yamlString = yaml.dump(parsed);
                        setYamlContent(yamlString);
                      } catch (yamlError) {
                        console.error('Error converting to YAML:', yamlError);
                      }
                    } catch (e) {
                      console.warn('Invalid JSON in editor');
                    }
                  }}
                  options={{
                    minimap: { enabled: false },
                    formatOnPaste: true,
                    formatOnType: true,
                    automaticLayout: true,
                    scrollBeyondLastLine: false,
                    folding: true,
                    lineNumbers: 'on',
                    renderLineHighlight: 'all',
                    tabSize: 2,
                  }}
                />
              )}

              {viewMode === 'yaml' && (
                <Editor
                  height="70vh"
                  defaultLanguage="yaml"
                  value={yamlContent}
                  onChange={(value) => {
                    setYamlContent(value);
                    try {
                      const parsed = yaml.load(value);
                      if (parsed && typeof parsed === 'object') {
                        setFormValues(parsed);
                        if (parsed.classes) {
                          setExtractionSchema(parsed.classes);
                        }
                        if (parsed.rule_classes) {
                          setRuleSchema(parsed.rule_classes);
                        }
                        const jsonString = JSON.stringify(parsed, null, 2);
                        setJsonContent(jsonString);
                      }
                    } catch (e) {
                      console.warn('Invalid YAML in editor');
                    }
                  }}
                  options={{
                    minimap: { enabled: false },
                    formatOnPaste: true,
                    formatOnType: true,
                    automaticLayout: true,
                    scrollBeyondLastLine: false,
                    folding: true,
                    lineNumbers: 'on',
                    renderLineHighlight: 'all',
                    tabSize: 2,
                  }}
                />
              )}
            </SpaceBetween>
          </Container>
        )}
      </SpaceBetween>

      {/* Save as New Version Modal */}
      <Modal
        visible={showSaveAsNewModal}
        onDismiss={() => setShowSaveAsNewModal(false)}
        header="Save as New Version"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowSaveAsNewModal(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleSaveAsNew} loading={isSaving}>
                Save as New Version
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Description">
          <Input
            value={newVersionDescription}
            onChange={({ detail }) => setNewVersionDescription(detail.value)}
            placeholder={(() => {
              const currentVersionData = versions.find((v) => v.versionId === selectedVersion);
              const currentDescription = currentVersionData?.description;
              return currentDescription ? `${currentDescription} - Copy` : `${selectedVersion} - Copy`;
            })()}
          />
        </FormField>
      </Modal>

      {/* Reset to Default Modal */}
      <Modal
        visible={showResetModal}
        onDismiss={() => setShowResetModal(false)}
        header="Reset to Default"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowResetModal(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleResetToDefault} loading={isSaving}>
                Reset
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Box variant="span">
          Are you sure you want to reset version {selectedVersion} to default values? This will overwrite all current settings.
        </Box>
      </Modal>

      {/* Export Modal */}
      <Modal
        visible={showExportModal}
        onDismiss={() => setShowExportModal(false)}
        header="Export Configuration"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowExportModal(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleExport}>
                Export
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween direction="vertical" size="l">
          <FormField label="File format">
            <RadioGroup
              value={exportFormat}
              onChange={({ detail }) => setExportFormat(detail.value)}
              items={[
                { value: 'json', label: 'JSON' },
                { value: 'yaml', label: 'YAML' },
              ]}
            />
          </FormField>
          <FormField label="File name">
            <Input value={exportFileName} onChange={({ detail }) => setExportFileName(detail.value)} placeholder="configuration" />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Import Source Selection Modal */}
      <Modal
        visible={showImportSourceModal}
        onDismiss={() => setShowImportSourceModal(false)}
        header="Choose Import Source"
        footer={
          <Box float="right">
            <Button variant="link" onClick={() => setShowImportSourceModal(false)}>
              Cancel
            </Button>
          </Box>
        }
      >
        <SpaceBetween size="l">
          <Button variant="primary" onClick={handleLocalFileImport} iconName="upload" fullWidth>
            Import from Local File
          </Button>
          <Button onClick={handleConfigLibraryImportForCurrentVersion} iconName="folder" fullWidth>
            Import from Configuration Library
          </Button>
        </SpaceBetween>
      </Modal>

      {/* Compare Versions Modal */}
      <Modal
        visible={showCompareModal}
        onDismiss={() => setShowCompareModal(false)}
        size="max"
        header="Compare Configuration Versions"
        footer={
          <Box float="right">
            <Button onClick={() => setShowCompareModal(false)}>Close</Button>
          </Box>
        }
      >
        <CompareVersionsContent selectedVersions={selectedVersionsForCompare} versions={versions} />
      </Modal>

      {/* Bulk Delete Confirmation Modal */}
      <Modal
        visible={showBulkDeleteModal}
        onDismiss={() => setShowBulkDeleteModal(false)}
        size="medium"
        header="Confirm Delete"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setShowBulkDeleteModal(false)}>Cancel</Button>
              <Button variant="primary" onClick={confirmDeleteVersions}>
                Delete {versionsToDelete.length} Version{versionsToDelete.length > 1 ? 's' : ''}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>Are you sure you want to delete the following configuration version{versionsToDelete.length > 1 ? 's' : ''}?</Box>
          <Box>
            <strong>Versions to delete:</strong>{' '}
            {versionsToDelete
              .map((versionId) => {
                const versionData = versions.find((v) => v.versionId === versionId);
                return versionData?.description ? `${versionId} (${versionData.description})` : versionId;
              })
              .join(', ')}
          </Box>
          <Alert type="warning">This action cannot be undone. The configuration versions will be permanently deleted.</Alert>
        </SpaceBetween>
      </Modal>

      {/* Version Creation Confirmation Modal */}
      <Modal
        visible={showVersionConfirmationModal}
        onDismiss={() => setShowVersionConfirmationModal(false)}
        size="medium"
        header={confirmationModalType === 'import' ? 'Version Imported Successfully' : 'Version Created Successfully'}
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setShowVersionConfirmationModal(false)}>
              OK
            </Button>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="success">
            <Box>
              <Icon name="status-positive" />{' '}
              {confirmationModalType === 'import'
                ? 'Configuration version has been imported and created successfully!'
                : 'New configuration version has been created successfully!'}
            </Box>
          </Alert>
          <Box>
            <strong>Version ID:</strong> {newlyCreatedVersionId}
          </Box>
          <Box>
            {confirmationModalType === 'import'
              ? 'The imported configuration is now available in the configuration versions table above.'
              : 'The new version is now available in the configuration versions table above.'}
          </Box>
        </SpaceBetween>
      </Modal>

      {/* Activate Version Confirmation Modal */}
      <Modal
        visible={showActivateModal}
        onDismiss={() => setShowActivateModal(false)}
        size="medium"
        header="Confirm Activation"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setShowActivateModal(false)}>Cancel</Button>
              <Button variant="primary" onClick={confirmActivateVersion}>
                Activate Version
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>
            Are you sure you want to activate version{' '}
            <strong>
              {(() => {
                const versionData = versions.find((v) => v.versionId === versionToActivate);
                return versionData?.description ? `${versionToActivate} (${versionData.description})` : versionToActivate;
              })()}
            </strong>
            ?
          </Box>
          <Alert type="info">This version will become the active configuration used for all new document processing.</Alert>
        </SpaceBetween>
      </Modal>

      {/* Activate Version Success Modal */}
      <Modal
        visible={showActivateConfirmationModal}
        onDismiss={() => setShowActivateConfirmationModal(false)}
        size="medium"
        header="Version Activated Successfully"
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setShowActivateConfirmationModal(false)}>
              OK
            </Button>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="success">
            <Box>
              <Icon name="status-positive" /> Configuration version has been activated successfully!
            </Box>
          </Alert>
          <Box>
            <strong>Active Version:</strong> {activatedVersionId}
          </Box>
          <Box>This version is now being used for all new document processing.</Box>
        </SpaceBetween>
      </Modal>

      {/* Import as New Version Modal */}
      <Modal
        visible={showImportAsNewVersionModal}
        onDismiss={() => {
          setShowImportAsNewVersionModal(false);
          setImportedConfigForNewVersion(null);
          setNewVersionDescription('');
        }}
        header="Import Configuration as New Version"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => {
                  setShowImportAsNewVersionModal(false);
                  setImportedConfigForNewVersion(null);
                  setNewVersionDescription('');
                }}
              >
                Cancel
              </Button>
              <Button variant="primary" onClick={handleCreateVersionFromImport} loading={isSaving} disabled={!importedConfigForNewVersion}>
                Create Version
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Import Configuration" description="Choose import source">
            <SpaceBetween size="m">
              <Button
                variant="primary"
                onClick={() => document.getElementById('import-new-version-file').click()}
                iconName="upload"
                fullWidth
              >
                Import from Local File
              </Button>
              <Button onClick={handleConfigLibraryImport} iconName="folder" fullWidth>
                Import from Configuration Library
              </Button>
            </SpaceBetween>
            <input
              id="import-new-version-file"
              type="file"
              accept=".json,.yaml,.yml"
              style={{ display: 'none' }}
              onChange={handleImportFileForNewVersion}
            />
            {importedConfigForNewVersion && (
              <Box margin={{ top: 's' }}>
                <Alert type="success">Configuration imported successfully!</Alert>
              </Box>
            )}
          </FormField>

          <FormField label="Version Description (Optional)" description="Provide a description for this new configuration version">
            <Input
              value={newVersionDescription}
              onChange={({ detail }) => setNewVersionDescription(detail.value)}
              placeholder="New imported version"
            />
          </FormField>

          {importedConfigForNewVersion && (
            <FormField label="Configuration Preview" description="Review the merged configuration before creating the version">
              <ExpandableSection headerText="View merged configuration" variant="container">
                <CodeEditor
                  ace={ace}
                  language="json"
                  value={JSON.stringify(importedConfigForNewVersion, null, 2)}
                  readOnly
                  preferences={{
                    fontSize: 12,
                    showLineNumbers: true,
                    showGutter: true,
                  }}
                  loading={false}
                />
              </ExpandableSection>
            </FormField>
          )}
        </SpaceBetween>
      </Modal>

      {/* Configuration Library Browser Modal */}
      <Modal
        visible={showLibraryBrowserModal}
        onDismiss={() => setShowLibraryBrowserModal(false)}
        size="large"
        header="Configuration Library"
        footer={
          <Box float="right">
            <Button onClick={() => setShowLibraryBrowserModal(false)}>Cancel</Button>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box>
            <strong>Available Configurations</strong>
            <br />
            Select a configuration to import into your current workspace.
          </Box>

          {libraryLoading ? (
            <Box textAlign="center">
              <Spinner size="large" />
            </Box>
          ) : (
            <Table
              columnDefinitions={[
                {
                  id: 'name',
                  header: 'Configuration Name',
                  cell: (item) => item.name,
                  sortingField: 'name',
                },
                {
                  id: 'actions',
                  header: 'Actions',
                  cell: (item) => (
                    <SpaceBetween direction="horizontal" size="xs">
                      <Button
                        variant="primary"
                        size="small"
                        onClick={() =>
                          libraryImportContext === 'new'
                            ? handleLibraryConfigSelect(item.name)
                            : handleLibraryConfigSelectForCurrentVersion(item.name)
                        }
                      >
                        Import
                      </Button>
                      {item.hasReadme && (
                        <Button size="small" onClick={() => handleViewReadme(item.name)}>
                          View README
                        </Button>
                      )}
                    </SpaceBetween>
                  ),
                },
              ]}
              items={libraryConfigs}
              loadingText="Loading configurations..."
              empty={
                <Box textAlign="center" color="inherit">
                  <b>No configurations available</b>
                  <Box variant="p" color="inherit">
                    No configurations found in the library for the current pattern.
                  </Box>
                </Box>
              }
            />
          )}
        </SpaceBetween>
      </Modal>

      {/* README Modal */}
      <Modal
        visible={showReadmeModal}
        onDismiss={() => setShowReadmeModal(false)}
        size="large"
        header={`README - ${selectedLibraryConfig}`}
        footer={
          <Box float="right">
            <Button onClick={() => setShowReadmeModal(false)}>Close</Button>
          </Box>
        }
      >
        <Box>
          <ReactMarkdown>{readmeContent}</ReactMarkdown>
        </Box>
      </Modal>

      {/* Bulk Delete Success Modal */}
      <Modal
        visible={showBulkDeleteSuccessModal}
        onDismiss={() => setShowBulkDeleteSuccessModal(false)}
        size="medium"
        header="Versions Deleted Successfully"
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setShowBulkDeleteSuccessModal(false)}>
              OK
            </Button>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="success">
            <Box>
              <Icon name="status-positive" /> Configuration versions have been deleted successfully!
            </Box>
          </Alert>
          <Box>
            <strong>Deleted Versions:</strong> {deletedVersionsDisplay}
          </Box>
        </SpaceBetween>
      </Modal>
    </>
  );
};

export default ConfigurationLayout;
