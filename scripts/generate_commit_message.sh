#!/bin/bash
# Generate a commit message using AWS Bedrock (no kiro-cli dependency)
#
# Usage:
#   bash scripts/generate_commit_message.sh
#
# Environment variables:
#   COMMIT_MODEL_ID - Bedrock model to use (default: amazon.nova-lite-v1:0)
#                     Examples:
#                       amazon.nova-lite-v1:0
#                       anthropic.claude-3-haiku-20240307-v1:0
#   AWS_REGION      - AWS region for Bedrock (uses default if not set)

set -euo pipefail

MODEL_ID="${COMMIT_MODEL_ID:-amazon.nova-lite-v1:0}"

# Get the diff - prefer staged changes, fall back to unstaged
DIFF_STAT=$(git diff --cached --stat 2>/dev/null)
DIFF_CONTENT=$(git diff --cached 2>/dev/null)

if [ -z "$DIFF_STAT" ]; then
    DIFF_STAT=$(git diff --stat 2>/dev/null)
    DIFF_CONTENT=$(git diff 2>/dev/null)
fi

# If still empty, try diff against HEAD
if [ -z "$DIFF_STAT" ]; then
    DIFF_STAT=$(git diff HEAD --stat 2>/dev/null || echo "no diff available")
    DIFF_CONTENT=$(git diff HEAD 2>/dev/null || echo "")
fi

# Truncate diff content to ~4000 chars to stay within token limits
DIFF_TRUNCATED=$(echo "$DIFF_CONTENT" | head -c 4000)

# Build the prompt
PROMPT="Based on the following git diff, generate a single-line commit message following conventional commit format (e.g., feat:, fix:, docs:, refactor:, chore:, test:).
Return ONLY the commit message on a single line. No quotes, no explanation, no prefix like 'Commit message:'.

Files changed:
${DIFF_STAT}

Diff:
${DIFF_TRUNCATED}"

# Escape the prompt for JSON using jq
ESCAPED_PROMPT=$(echo "$PROMPT" | jq -Rs .)

# Build the messages JSON
MESSAGES="[{\"role\":\"user\",\"content\":[{\"text\":${ESCAPED_PROMPT}}]}]"

# Call Bedrock converse API
COMMIT_MSG=$(aws bedrock-runtime converse \
    --model-id "$MODEL_ID" \
    --messages "$MESSAGES" \
    --inference-config '{"maxTokens":100,"temperature":0.3}' \
    --query 'output.message.content[0].text' \
    --output text 2>&1) || true

# Fallback if Bedrock call fails or returns empty
if [ -z "$COMMIT_MSG" ] || [ "$COMMIT_MSG" = "None" ] || echo "$COMMIT_MSG" | grep -qi "error"; then
    # Generate a basic fallback message from the diff stat
    FILE_COUNT=$(echo "$DIFF_STAT" | grep -c "file" || echo "0")
    echo "chore: update ${FILE_COUNT} file(s)"
    exit 0
fi

# Clean up - remove surrounding quotes if present, trim whitespace
COMMIT_MSG=$(echo "$COMMIT_MSG" | sed 's/^["'"'"']//;s/["'"'"']$//' | xargs)

echo "$COMMIT_MSG"
