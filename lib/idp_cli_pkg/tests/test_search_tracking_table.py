# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Unit tests for TrackingTableSearcher class.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest

from idp_cli.search_tracking_table import TrackingTableSearcher


@pytest.fixture
def mock_stack_info():
    """Mock StackInfo to avoid AWS calls."""
    with patch("idp_cli.search_tracking_table.StackInfo") as mock:
        mock_instance = Mock()
        mock_instance.get_resources.return_value = {
            "DocumentsTable": "test-documents-table"
        }
        mock.return_value = mock_instance
        yield mock


@pytest.fixture
def mock_dynamodb():
    """Mock boto3 DynamoDB client."""
    with patch("idp_cli.search_tracking_table.boto3.Session") as mock_session:
        mock_client = Mock()
        mock_session.return_value.client.return_value = mock_client
        yield mock_client


@pytest.fixture
def searcher(mock_stack_info, mock_dynamodb):
    """Create a TrackingTableSearcher instance with mocked dependencies."""
    return TrackingTableSearcher(stack_name="test-stack", region="us-east-1")


class TestTrackingTableSearcher:
    """Test TrackingTableSearcher class."""

    def test_initialization(self, searcher, mock_stack_info):
        """Test searcher initialization."""
        assert searcher.stack_name == "test-stack"
        assert searcher.region == "us-east-1"
        assert searcher.table_name == "test-documents-table"
        mock_stack_info.assert_called_once_with("test-stack", "us-east-1")

    def test_search_by_pk_and_status_success(self, searcher, mock_dynamodb):
        """Test successful search with results."""
        # Mock DynamoDB response
        mock_dynamodb.scan.return_value = {
            "Items": [
                {
                    "PK": {"S": "doc#batch-123/invoice.pdf"},
                    "ObjectKey": {"S": "batch-123/invoice.pdf"},
                    "ObjectStatus": {"S": "COMPLETED"},
                },
                {
                    "PK": {"S": "doc#batch-123/receipt.pdf"},
                    "ObjectKey": {"S": "batch-123/receipt.pdf"},
                    "ObjectStatus": {"S": "COMPLETED"},
                },
            ],
            "Count": 2,
        }

        result = searcher.search_by_pk_and_status(
            pk="batch-123", object_status="COMPLETED"
        )

        assert result["success"] is True
        assert result["count"] == 2
        assert result["pk"] == "batch-123"
        assert result["object_status"] == "COMPLETED"
        assert len(result["items"]) == 2

        # Verify scan was called with correct parameters
        mock_dynamodb.scan.assert_called_once()
        call_args = mock_dynamodb.scan.call_args[1]
        assert call_args["TableName"] == "test-documents-table"
        assert "FilterExpression" in call_args
        assert call_args["ExpressionAttributeValues"][":pk"]["S"] == "batch-123"
        assert (
            call_args["ExpressionAttributeValues"][":status"]["S"] == "COMPLETED"
        )

    def test_search_by_pk_and_status_with_pagination(self, searcher, mock_dynamodb):
        """Test search with pagination."""
        # Mock paginated responses
        mock_dynamodb.scan.side_effect = [
            {
                "Items": [
                    {
                        "PK": {"S": "doc#batch-123/doc1.pdf"},
                        "ObjectKey": {"S": "batch-123/doc1.pdf"},
                        "ObjectStatus": {"S": "COMPLETED"},
                    }
                ],
                "LastEvaluatedKey": {"PK": {"S": "doc#batch-123/doc1.pdf"}},
            },
            {
                "Items": [
                    {
                        "PK": {"S": "doc#batch-123/doc2.pdf"},
                        "ObjectKey": {"S": "batch-123/doc2.pdf"},
                        "ObjectStatus": {"S": "COMPLETED"},
                    }
                ],
            },
        ]

        result = searcher.search_by_pk_and_status(
            pk="batch-123", object_status="COMPLETED"
        )

        assert result["success"] is True
        assert result["count"] == 2
        assert len(result["items"]) == 2
        assert mock_dynamodb.scan.call_count == 2

    def test_search_by_pk_and_status_no_results(self, searcher, mock_dynamodb):
        """Test search with no results."""
        mock_dynamodb.scan.return_value = {"Items": [], "Count": 0}

        result = searcher.search_by_pk_and_status(
            pk="nonexistent", object_status="COMPLETED"
        )

        assert result["success"] is True
        assert result["count"] == 0
        assert len(result["items"]) == 0

    def test_search_by_pk_and_status_error(self, searcher, mock_dynamodb):
        """Test search with DynamoDB error."""
        mock_dynamodb.scan.side_effect = Exception("DynamoDB error")

        result = searcher.search_by_pk_and_status(
            pk="batch-123", object_status="COMPLETED"
        )

        assert result["success"] is False
        assert "error" in result
        assert "DynamoDB error" in result["error"]

    def test_search_by_pk_and_status_no_table(self, mock_stack_info, mock_dynamodb):
        """Test search when table is not found."""
        mock_stack_info.return_value.get_resources.return_value = {}

        searcher = TrackingTableSearcher(stack_name="test-stack", region="us-east-1")
        result = searcher.search_by_pk_and_status(
            pk="batch-123", object_status="COMPLETED"
        )

        assert result["success"] is False
        assert "DocumentsTable not found" in result["error"]

    def test_calculate_timing_statistics_success(self, searcher):
        """Test timing statistics calculation with valid data."""
        # Create mock search results with timing data
        from datetime import timedelta

        now = datetime.now()
        queued_time = now.isoformat()
        workflow_start = (now + timedelta(seconds=10)).isoformat()
        completion_time = (now + timedelta(seconds=60)).isoformat()

        search_results = {
            "success": True,
            "count": 2,
            "items": [
                {
                    "ObjectKey": {"S": "batch-123/doc1.pdf"},
                    "QueuedTime": {"S": queued_time},
                    "WorkflowStartTime": {"S": workflow_start},
                    "CompletionTime": {"S": completion_time},
                },
                {
                    "ObjectKey": {"S": "batch-123/doc2.pdf"},
                    "QueuedTime": {"S": queued_time},
                    "WorkflowStartTime": {"S": workflow_start},
                    "CompletionTime": {"S": completion_time},
                },
            ],
        }

        stats = searcher.calculate_timing_statistics(
            search_results, include_metering=False
        )

        assert stats["success"] is True
        assert stats["valid_count"] == 2
        assert "processing_time" in stats
        assert "queue_time" in stats
        assert "total_time" in stats

        # Check processing time stats
        pt = stats["processing_time"]
        assert "average" in pt
        assert "median" in pt
        assert "min" in pt
        assert "max" in pt
        assert "min_key" in pt
        assert "max_key" in pt

    def test_calculate_timing_statistics_missing_data(self, searcher):
        """Test timing statistics with missing timestamp data."""
        search_results = {
            "success": True,
            "count": 2,
            "items": [
                {
                    "ObjectKey": {"S": "batch-123/doc1.pdf"},
                    # Missing timestamps
                },
                {
                    "ObjectKey": {"S": "batch-123/doc2.pdf"},
                    "WorkflowStartTime": {"S": "2025-01-01T10:00:00"},
                    # Missing CompletionTime
                },
            ],
        }

        stats = searcher.calculate_timing_statistics(
            search_results, include_metering=False
        )

        assert stats["success"] is False
        assert "error" in stats
        assert "missing required timestamps" in stats["error"]

    def test_calculate_timing_statistics_with_metering(self, searcher):
        """Test timing statistics with Lambda metering data."""
        from datetime import timedelta

        now = datetime.now()
        workflow_start = now.isoformat()
        completion_time = (now + timedelta(seconds=60)).isoformat()

        # Mock metering data in DynamoDB Map format
        metering_data = {
            "M": {
                "Assessment/lambda/duration": {
                    "M": {"gb_seconds": {"N": "1.5"}, "duration_ms": {"N": "1500"}}
                },
                "OCR/lambda/duration": {
                    "M": {"gb_seconds": {"N": "2.5"}, "duration_ms": {"N": "2500"}}
                },
            }
        }

        search_results = {
            "success": True,
            "count": 1,
            "items": [
                {
                    "ObjectKey": {"S": "batch-123/doc1.pdf"},
                    "WorkflowStartTime": {"S": workflow_start},
                    "CompletionTime": {"S": completion_time},
                    "Metering": metering_data,
                }
            ],
        }

        stats = searcher.calculate_timing_statistics(
            search_results, include_metering=True
        )

        assert stats["success"] is True
        assert "metering" in stats
        assert stats["metering_count"] == 1

    def test_calculate_timing_statistics_no_results(self, searcher):
        """Test timing statistics with no results."""
        search_results = {"success": True, "count": 0, "items": []}

        stats = searcher.calculate_timing_statistics(
            search_results, include_metering=False
        )

        assert stats["success"] is False
        assert "No results to analyze" in stats["error"]

    def test_parse_dynamodb_map(self, searcher):
        """Test DynamoDB Map parsing."""
        dynamodb_map = {
            "string_field": {"S": "test_value"},
            "number_field": {"N": "123.45"},
            "bool_field": {"BOOL": True},
            "null_field": {"NULL": True},
            "nested_map": {
                "M": {
                    "inner_string": {"S": "inner_value"},
                    "inner_number": {"N": "67.89"},
                }
            },
            "list_field": {"L": [{"S": "item1"}, {"N": "2"}, {"BOOL": False}]},
        }

        result = searcher._parse_dynamodb_map(dynamodb_map)

        assert result["string_field"] == "test_value"
        assert result["number_field"] == 123.45
        assert result["bool_field"] is True
        assert result["null_field"] is None
        assert result["nested_map"]["inner_string"] == "inner_value"
        assert result["nested_map"]["inner_number"] == 67.89
        assert result["list_field"] == ["item1", 2.0, False]

    @patch("idp_cli.search_tracking_table.console")
    def test_display_results_success(self, mock_console, searcher):
        """Test display_results with successful results."""
        results = {
            "success": True,
            "count": 2,
            "pk": "batch-123",
            "object_status": "COMPLETED",
            "items": [
                {
                    "ObjectKey": {"S": "batch-123/doc1.pdf"},
                    "ObjectStatus": {"S": "COMPLETED"},
                    "PK": {"S": "doc#batch-123/doc1.pdf"},
                }
            ],
        }

        searcher.display_results(results, show_details=False)

        # Verify console.print was called
        assert mock_console.print.called

    @patch("idp_cli.search_tracking_table.console")
    def test_display_results_error(self, mock_console, searcher):
        """Test display_results with error."""
        results = {"success": False, "error": "Test error"}

        searcher.display_results(results, show_details=False)

        # Verify error message was printed
        mock_console.print.assert_called()
        call_args = str(mock_console.print.call_args)
        assert "error" in call_args.lower()

    @patch("idp_cli.search_tracking_table.console")
    def test_display_timing_statistics(self, mock_console, searcher):
        """Test display_timing_statistics."""
        stats = {
            "success": True,
            "valid_count": 10,
            "processing_time": {
                "average": 45.5,
                "median": 43.0,
                "min": 30.0,
                "min_key": "batch-123/fast.pdf",
                "max": 80.0,
                "max_key": "batch-123/slow.pdf",
                "stdev": 12.5,
                "total": 455.0,
            },
        }

        searcher.display_timing_statistics(stats)

        # Verify console.print was called multiple times
        assert mock_console.print.call_count > 0


class TestIntegration:
    """Integration-style tests for common workflows."""

    def test_search_and_calculate_timing_workflow(self, searcher, mock_dynamodb):
        """Test complete workflow: search then calculate timing."""
        from datetime import timedelta

        # Mock search response
        now = datetime.now()
        workflow_start = now.isoformat()
        completion_time = (now + timedelta(seconds=60)).isoformat()

        mock_dynamodb.scan.return_value = {
            "Items": [
                {
                    "PK": {"S": "doc#batch-123/doc1.pdf"},
                    "ObjectKey": {"S": "batch-123/doc1.pdf"},
                    "ObjectStatus": {"S": "COMPLETED"},
                    "WorkflowStartTime": {"S": workflow_start},
                    "CompletionTime": {"S": completion_time},
                }
            ]
        }

        # Step 1: Search
        search_results = searcher.search_by_pk_and_status(
            pk="batch-123", object_status="COMPLETED"
        )
        assert search_results["success"] is True
        assert search_results["count"] == 1

        # Step 2: Calculate timing
        timing_stats = searcher.calculate_timing_statistics(
            search_results, include_metering=False
        )
        assert timing_stats["success"] is True
        assert "processing_time" in timing_stats

    def test_empty_search_results_workflow(self, searcher, mock_dynamodb):
        """Test workflow with no search results."""
        mock_dynamodb.scan.return_value = {"Items": [], "Count": 0}

        # Step 1: Search
        search_results = searcher.search_by_pk_and_status(
            pk="nonexistent", object_status="COMPLETED"
        )
        assert search_results["success"] is True
        assert search_results["count"] == 0

        # Step 2: Try to calculate timing (should fail gracefully)
        timing_stats = searcher.calculate_timing_statistics(
            search_results, include_metering=False
        )
        assert timing_stats["success"] is False
        assert "No results to analyze" in timing_stats["error"]
