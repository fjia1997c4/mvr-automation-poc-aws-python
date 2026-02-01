// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import React, { useState } from 'react';
import PropTypes from 'prop-types';
import { Table, Box, SpaceBetween, Badge, Link, Button, Header, Pagination, TextFilter } from '@cloudscape-design/components';
import { useCollection } from '@cloudscape-design/collection-hooks';

const ConfigurationVersionsTable = ({
  versions = [],
  loading = false,
  onVersionSelect,
  selectedVersionsForCompare = [],
  onVersionSelectForCompare,
  onCompareVersions,
  onActivateVersion,
  onDeleteVersions,
  onImportAsNewVersion,
}) => {
  // Log the versions data to console for debugging
  console.log('ConfigurationVersionsTable - versions data:', versions);
  console.log('ConfigurationVersionsTable - loading:', loading);

  const columnDefinitions = [
    {
      id: 'select',
      header: (
        <input
          type="checkbox"
          checked={selectedVersionsForCompare.length === versions.length && versions.length > 0}
          onChange={(e) => {
            if (e.target.checked) {
              // Select all versions
              const allVersionIds = versions.map((v) => v.versionId);
              allVersionIds.forEach((versionId) => {
                if (!selectedVersionsForCompare.includes(versionId)) {
                  onVersionSelectForCompare?.(versionId, true);
                }
              });
            } else {
              // Deselect all versions
              selectedVersionsForCompare.forEach((versionId) => {
                onVersionSelectForCompare?.(versionId, false);
              });
            }
          }}
          title="Select/Deselect All"
        />
      ),
      cell: (item) => (
        <input
          type="checkbox"
          checked={selectedVersionsForCompare.includes(item.versionId)}
          onChange={(e) => onVersionSelectForCompare?.(item.versionId, e.target.checked)}
        />
      ),
      width: 80,
    },
    {
      id: 'versionId',
      header: 'Version ID',
      cell: (item) => (
        <Link
          href="#"
          onFollow={(event) => {
            event.preventDefault();
            onVersionSelect?.(item.versionId);
          }}
        >
          <Box fontWeight={item.isActive ? 'bold' : 'normal'} color={item.isActive ? 'text-status-success' : 'inherit'}>
            {item.versionId}
            {item.isActive ? ' (Active)' : ''}
          </Box>
        </Link>
      ),
      sortingField: 'versionId',
    },
    {
      id: 'description',
      header: 'Description',
      cell: (item) => item.description || '-',
    },
    {
      id: 'createdAt',
      header: 'Created',
      cell: (item) => (item.createdAt ? new Date(item.createdAt).toLocaleString() : '-'),
      sortingField: 'createdAt',
    },
    {
      id: 'updatedAt',
      header: 'Updated',
      cell: (item) => (item.updatedAt ? new Date(item.updatedAt).toLocaleString() : '-'),
      sortingField: 'updatedAt',
    },
  ];

  const { items, collectionProps, paginationProps, filteredItemsCount, filterProps } = useCollection(versions, {
    pagination: { pageSize: 5 },
    sorting: {
      defaultState: {
        sortingColumn: columnDefinitions[4], // Sort by updatedAt by default
        isDescending: true,
      },
    },
    filtering: {
      empty: (
        <Box margin={{ vertical: 'xs' }} textAlign="center" color="inherit">
          <SpaceBetween size="m">
            <b>No matches</b>
            <Box variant="p" color="inherit">
              We can&apos;t find a match.
            </Box>
          </SpaceBetween>
        </Box>
      ),
      noMatch: (
        <Box margin={{ vertical: 'xs' }} textAlign="center" color="inherit">
          <SpaceBetween size="m">
            <b>No matches</b>
            <Box variant="p" color="inherit">
              We can&apos;t find a match.
            </Box>
          </SpaceBetween>
        </Box>
      ),
    },
  });

  return (
    <Table
      {...collectionProps}
      columnDefinitions={columnDefinitions}
      items={items}
      loading={loading}
      loadingText="Loading versions..."
      resizableColumns
      stripedRows
      empty={
        <Box margin={{ vertical: 'xs' }} textAlign="center" color="inherit">
          <SpaceBetween size="m">
            <b>No versions</b>
            <Box variant="p" color="inherit">
              No configuration versions found.
            </Box>
          </SpaceBetween>
        </Box>
      }
      header={
        <Header
          variant="h2"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={onCompareVersions} disabled={selectedVersionsForCompare.length < 2}>
                Compare Selected ({selectedVersionsForCompare.length})
              </Button>
              <Button
                onClick={() => onActivateVersion?.(selectedVersionsForCompare[0])}
                disabled={
                  selectedVersionsForCompare.length !== 1 || versions.find((v) => v.versionId === selectedVersionsForCompare[0])?.isActive
                }
              >
                Activate
              </Button>
              <Button variant="normal" onClick={() => onImportAsNewVersion?.()} iconName="upload">
                Import as New Version
              </Button>
              <Button
                variant="primary"
                onClick={() => onDeleteVersions?.(selectedVersionsForCompare)}
                disabled={
                  selectedVersionsForCompare.length === 0 ||
                  selectedVersionsForCompare.includes('v0') ||
                  selectedVersionsForCompare.some((vId) => versions.find((v) => v.versionId === vId)?.isActive)
                }
              >
                Delete Selected ({selectedVersionsForCompare.length})
              </Button>
            </SpaceBetween>
          }
        >
          Configuration Versions ({filteredItemsCount})
        </Header>
      }
      filter={<TextFilter {...filterProps} placeholder="Search versions..." />}
      pagination={<Pagination {...paginationProps} />}
    />
  );
};

ConfigurationVersionsTable.propTypes = {
  versions: PropTypes.arrayOf(
    PropTypes.shape({
      versionId: PropTypes.string.isRequired,
      isActive: PropTypes.bool,
      createdAt: PropTypes.string,
      updatedAt: PropTypes.string,
      description: PropTypes.string,
    }),
  ),
  loading: PropTypes.bool,
  onVersionSelect: PropTypes.func,
  selectedVersionsForCompare: PropTypes.arrayOf(PropTypes.string),
  onVersionSelectForCompare: PropTypes.func,
  onCompareVersions: PropTypes.func,
  onActivateVersion: PropTypes.func,
  onDeleteVersions: PropTypes.func,
  onImportAsNewVersion: PropTypes.func,
};

export default ConfigurationVersionsTable;
