#!/usr/bin/env bash
# Generate an idp-config file from a deployed IDP-accelerator CloudFormation stack.
#
# Usage:
#   gen-idp-config.sh [-r REGION] [-c CLIENT_NAME_OR_ID] [-u USERNAME] [-o OUTPUT_FILE] STACK_NAME
#
# Defaults:
#   -r us-east-1
#   -c external-app-client
#   -u idp-gateway-service-user
#   -o /dev/stdout
#
# Password: read from $IDP_PASSWORD if set. Never accepted on the command line
# (would land in shell history — NIST IA-5). If unset, an empty value is written
# and the caller must fill it in before use.
#
# AWS credentials: uses the caller's ambient AWS CLI configuration. The required
# permissions are read-only:
#   cloudformation:DescribeStacks
#   cognito-idp:ListUserPoolClients
#   cognito-idp:DescribeUserPoolClient
#
# Output format (one key=value per line):
#   appsync.endpoint
#   s3.outputBucket
#   cognito.region
#   cognito.userPoolId
#   cognito.clientId
#   cognito.clientSecret
#   cognito.username
#   cognito.password

set -euo pipefail

REGION="us-east-1"
CLIENT="external-app-client"
USERNAME="idp-gateway-service-user"
OUTPUT="/dev/stdout"

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-2}"
}

while getopts ":r:c:u:o:h" opt; do
  case "$opt" in
    r) REGION="$OPTARG" ;;
    c) CLIENT="$OPTARG" ;;
    u) USERNAME="$OPTARG" ;;
    o) OUTPUT="$OPTARG" ;;
    h) usage 0 ;;
    :) echo "missing argument for -$OPTARG" >&2; usage ;;
    \?) echo "unknown option: -$OPTARG" >&2; usage ;;
  esac
done
shift $((OPTIND - 1))

if [[ $# -ne 1 ]]; then
  echo "error: STACK_NAME is required" >&2
  usage
fi
STACK_NAME="$1"

for cmd in aws jq; do
  command -v "$cmd" >/dev/null || { echo "error: $cmd not found in PATH" >&2; exit 3; }
done

# --- Read stack outputs --------------------------------------------------

outputs_json="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'Stacks[0].Outputs' \
  --output json)"

get_output() {
  local key="$1"
  jq -r --arg k "$key" '.[] | select(.OutputKey==$k) | .OutputValue' <<<"$outputs_json"
}

web_env="$(get_output WebUITestEnvFile)"
output_bucket="$(get_output S3OutputBucketName)"

if [[ -z "$web_env" ]]; then
  echo "error: stack '$STACK_NAME' has no WebUITestEnvFile output — not an IDP accelerator stack?" >&2
  exit 4
fi
if [[ -z "$output_bucket" ]]; then
  echo "error: stack '$STACK_NAME' has no S3OutputBucketName output" >&2
  exit 4
fi

# WebUITestEnvFile is a VITE_*=… block. Parse the fields we need.
extract_env() {
  local key="$1"
  awk -F= -v k="$key" '$1==k { sub(/^[^=]*=/,""); print; exit }' <<<"$web_env"
}

appsync_url="$(extract_env VITE_APPSYNC_GRAPHQL_URL)"
user_pool_id="$(extract_env VITE_USER_POOL_ID)"
env_region="$(extract_env VITE_AWS_REGION)"

# Prefer the region reported by the stack; fall back to the CLI flag.
if [[ -n "$env_region" ]]; then
  cognito_region="$env_region"
else
  cognito_region="$REGION"
fi

for v in appsync_url user_pool_id cognito_region output_bucket; do
  if [[ -z "${!v}" ]]; then
    echo "error: could not derive $v from stack outputs" >&2
    exit 5
  fi
done

# --- Resolve client ID + secret -----------------------------------------

# Accept either a client name or a raw client ID (Cognito client IDs are
# lowercase alphanumeric, typically 26 chars). Use ListUserPoolClients to
# resolve a name → ID; if -c looks like an ID and matches an existing client,
# accept it as-is.
clients_json="$(aws cognito-idp list-user-pool-clients \
  --user-pool-id "$user_pool_id" \
  --region "$cognito_region" \
  --max-results 60 \
  --output json)"

client_id="$(jq -r --arg n "$CLIENT" \
  '.UserPoolClients[] | select(.ClientName==$n) | .ClientId' <<<"$clients_json")"

if [[ -z "$client_id" ]]; then
  # Maybe -c was already a client ID.
  client_id="$(jq -r --arg i "$CLIENT" \
    '.UserPoolClients[] | select(.ClientId==$i) | .ClientId' <<<"$clients_json")"
fi

if [[ -z "$client_id" ]]; then
  echo "error: no user pool client matches '$CLIENT' in pool $user_pool_id" >&2
  echo "available clients:" >&2
  jq -r '.UserPoolClients[] | "  \(.ClientName)  \(.ClientId)"' <<<"$clients_json" >&2
  exit 6
fi

client_secret="$(aws cognito-idp describe-user-pool-client \
  --user-pool-id "$user_pool_id" \
  --client-id "$client_id" \
  --region "$cognito_region" \
  --query 'UserPoolClient.ClientSecret' \
  --output text)"

# describe-user-pool-client prints "None" (literal) when the client has no secret.
if [[ "$client_secret" == "None" ]]; then
  client_secret=""
  echo "warning: client '$CLIENT' has no generated secret — cognito.clientSecret will be empty" >&2
fi

# --- Emit ---------------------------------------------------------------

password="${IDP_PASSWORD-}"

# Write to a temp file with 0600 then move into place, so the secret never
# exists on disk world-readable (NIST AU-9 / SC-28 spirit).
emit() {
  cat <<EOF
appsync.endpoint=$appsync_url
s3.outputBucket=s3://$output_bucket/
cognito.region=$cognito_region
cognito.userPoolId=$user_pool_id
cognito.clientId=$client_id
cognito.clientSecret=$client_secret
cognito.username=$USERNAME
cognito.password=$password
EOF
}

if [[ "$OUTPUT" == "/dev/stdout" || "$OUTPUT" == "-" ]]; then
  emit
else
  tmp="$(mktemp "${OUTPUT}.XXXXXX")"
  chmod 600 "$tmp"
  emit >"$tmp"
  mv "$tmp" "$OUTPUT"
  chmod 600 "$OUTPUT"
  echo "wrote $OUTPUT" >&2
fi
