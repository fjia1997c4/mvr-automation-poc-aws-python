// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import type { DocumentNode } from 'graphql';
import gql from 'graphql-tag';

const syncBdaIdp: DocumentNode = gql`
  mutation SyncBdaIdp($direction: String, $versionName: String, $bdaProjectArn: String, $saveArn: Boolean, $syncMode: String) {
    syncBdaIdp(direction: $direction, versionName: $versionName, bdaProjectArn: $bdaProjectArn, saveArn: $saveArn, syncMode: $syncMode) {
      success
      message
      processedClasses
      direction
      bdaProjectArn
      bdaSyncStatus
      error {
        type
        message
      }
    }
  }
`;

export default syncBdaIdp;
