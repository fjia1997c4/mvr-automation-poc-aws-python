# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Batch confidence scores retrieval tool"""

import logging
import os
from typing import Any, Dict, Optional

from .base import IDPTool

logger = logging.getLogger(__name__)


class GetConfidenceTool(IDPTool):
    """Get confidence scores for all documents in batch"""

    def __init__(self):
        """Initialize with stack name from environment"""
        self.stack_name = os.environ.get("AWS_STACK_NAME")
        if not self.stack_name:
            lambda_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
            if lambda_name.endswith("-agentcore-analytics"):
                self.stack_name = lambda_name.replace("-agentcore-analytics", "")
            else:
                self.stack_name = lambda_name
        logger.info(f"GetConfidenceTool initialized with stack_name: {self.stack_name}")

    def execute(
        self,
        batch_id: str,
        section_id: int = 1,
        limit: int = 10,
        next_token: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute confidence retrieval for batch documents"""
        try:
            from idp_sdk.client import IDPClient

            logger.info(
                f"Retrieving confidence scores for batch: {batch_id}, "
                f"section: {section_id}, limit: {limit}"
            )

            client = IDPClient(stack_name=self.stack_name)
            result = client.batch.get_confidence(
                batch_id=batch_id,
                section_id=section_id,
                limit=limit,
                next_token=next_token,
            )

            return {
                "success": True,
                "batch_id": result.get("batch_id"),
                "section_id": result.get("section_id"),
                "count": result.get("count"),
                "total_in_batch": result.get("total_in_batch"),
                "documents": result.get("documents", []),
                "summary": result.get("summary", {}),
                "next_token": result.get("next_token"),
                "message": f"Retrieved confidence scores for {result.get('count', 0)} documents",
            }

        except Exception as e:
            logger.error(f"Confidence retrieval failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": "confidence_retrieval_failed",
                "message": f"Failed to retrieve confidence scores: {str(e)}",
            }


