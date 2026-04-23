# UI Authentication & Authorization — End-to-End Sequence

This document traces the data flow from a user visiting the GenAIIDP UI login page through to an **authenticated and authorized** backend call (GraphQL query/mutation, S3 upload, etc.).

There are two distinct concerns:

1. **Authentication (AuthN)** — *Who are you?* Proves identity via Cognito User Pool (username/password SRP) or an external SAML/OIDC IdP federated through Cognito. Result: a Cognito-issued **ID token** (JWT) containing the user's email, `sub`, and `cognito:groups` claim.
2. **Authorization (AuthZ)** — *What can you do?* Two layers:
   - **Transport-level**: AppSync validates the ID token on every request (via the `AMAZON_COGNITO_USER_POOLS` auth type) and GraphQL types are gated by `@aws_cognito_user_pools`.
   - **Role-level (RBAC)**: Cognito groups (`Admin`, `Author`, `Reviewer`, `Viewer`) are inspected — client-side for UI rendering and server-side inside Lambda resolvers (via `event.identity.claims['cognito:groups']`) — to filter data and gate mutations.
3. **AWS resource access** — After sign-in, Amplify exchanges the ID token for **temporary AWS credentials** from the Cognito Identity Pool (`CognitoAuthorizedRole`). These are used for direct-to-S3 reads and for IAM-signed paths on AppSync.

---

## 1. Authentication — from login page to ID token

```mermaid
sequenceDiagram
    autonumber
    participant Browser
    participant App as App.tsx<br/>(Authenticator.Provider)
    participant Routes as Routes.tsx<br/>(guard)
    participant Unauth as UnauthRoutes.tsx<br/>AutoLoginOrAuthenticator
    participant Amplify as AWS Amplify<br/>(aws-amplify/ui-react)
    participant Cognito as Cognito<br/>User Pool
    participant Hosted as Cognito<br/>Hosted UI / OAuth
    participant IdP as External IdP<br/>(SAML/OIDC)<br/>[optional]
    participant PreTok as PreTokenGeneration<br/>Lambda<br/>[if federated]
    participant IDPool as Cognito<br/>Identity Pool
    participant STS as AWS STS

    Browser->>App: GET / (React bundle loads)
    Note right of App: App.tsx:72 wraps tree in<br/><Authenticator.Provider><br/>aws-exports.js is imported<br/>as Amplify.configure() input

    App->>Routes: render <Routes />
    Routes->>Routes: authStatus !== 'authenticated'<br/>|| !user || !currentCredentials<br/>(Routes.tsx:42)
    Routes->>Unauth: render <UnauthRoutes />

    alt External IdP with auto-login (UnauthRoutes.tsx:53)
        Unauth->>Amplify: signInWithRedirect({provider:{custom: VITE_EXTERNAL_IDP_NAME}})
        Amplify->>Hosted: 302 → {domain}/oauth2/authorize<br/>?identity_provider=<IdP>&response_type=code<br/>&client_id=<UserPoolClientId><br/>&redirect_uri=<redirectSignIn>&scope=openid+email+phone+profile
        Hosted->>IdP: SAML AuthnRequest / OIDC authorize
        IdP-->>Browser: Login form
        Browser->>IdP: Credentials (+ MFA if enforced by IdP)
        IdP-->>Hosted: SAML assertion / OIDC id_token<br/>(includes IdP groups)
        Hosted->>Cognito: Link external identity<br/>(creates/updates user with<br/>custom:idp_groups attribute)
        Cognito->>PreTok: Invoke PreTokenGeneration<br/>(template.yaml:5828-5839)
        PreTok-->>Cognito: Add mapped Cognito groups<br/>(Admin/Author/Reviewer/Viewer)<br/>to cognito:groups claim
        Hosted-->>Browser: 302 → redirectSignIn?code=...
        Browser->>Amplify: Callback with ?code
        Amplify->>Cognito: POST /oauth2/token<br/>grant_type=authorization_code, code, redirect_uri
        Cognito-->>Amplify: { id_token, access_token, refresh_token }
    else Username/password (Authenticator UI)
        Unauth->>Browser: Render <Authenticator initialState="signIn"><br/>(UnauthRoutes.tsx:65)
        Browser->>Amplify: Submit username + password
        Amplify->>Cognito: InitiateAuth (USER_SRP_AUTH)
        Cognito-->>Amplify: SRP challenge
        Amplify->>Cognito: RespondToAuthChallenge (SRP proof)
        Cognito-->>Amplify: { id_token, access_token, refresh_token }
        Note right of Cognito: Client has<br/>ALLOW_USER_SRP_AUTH +<br/>ALLOW_REFRESH_TOKEN_AUTH<br/>(template.yaml:5896-5898)
    end

    Note over Amplify: Amplify stores tokens in localStorage:<br/>CognitoIdentityServiceProvider.<br/>{clientId}.{sub}.idToken / accessToken / refreshToken

    App->>Amplify: useCurrentSessionCreds()<br/>→ fetchAuthSession()<br/>(use-current-session-creds.ts:25)
    Amplify->>IDPool: GetId / GetCredentialsForIdentity<br/>Logins: { cognito-idp.<region>.amazonaws.com/<UserPoolId>: idToken }
    IDPool->>STS: AssumeRoleWithWebIdentity<br/>→ CognitoAuthorizedRole<br/>(template.yaml:6283-6347)
    STS-->>IDPool: { AccessKeyId, SecretAccessKey, SessionToken, Expiration }
    IDPool-->>Amplify: AWS temp credentials
    Amplify-->>App: { tokens: {idToken, accessToken, refreshToken},<br/>credentials: {...} }

    App->>Routes: authStatus='authenticated',<br/>user set, currentCredentials set
    Routes->>Routes: render <AuthRoutes /><br/>(Routes.tsx:45)

    Note over Browser,STS: ID token payload now contains:<br/>sub, email, cognito:username,<br/>cognito:groups: ["Admin"|"Author"|"Reviewer"|"Viewer"],<br/>identities (present iff federated),<br/>exp (1 hour, template.yaml:5900)<br/><br/>Amplify refreshes tokens via refresh_token (30-day validity)<br/>and credentials every 15 min (use-current-session-creds.ts:9)
```

### Key AuthN files

| Concern | File | Ref |
|---|---|---|
| Amplify setup | `src/ui/src/aws-exports.js` | [aws-exports.js:32-51](../src/ui/src/aws-exports.js#L32-L51) — `authenticationType: 'AMAZON_COGNITO_USER_POOLS'`, identity pool id, OAuth block (only if `VITE_EXTERNAL_IDP_NAME` set) |
| Root auth provider | `src/ui/src/App.tsx` | [App.tsx:72](../src/ui/src/App.tsx#L72) — `<Authenticator.Provider>` wraps app |
| Guard | `src/ui/src/routes/Routes.tsx` | [Routes.tsx:42](../src/ui/src/routes/Routes.tsx#L42) — requires `authStatus==='authenticated' && user && currentCredentials` |
| Login UI + federation redirect | `src/ui/src/routes/UnauthRoutes.tsx` | [UnauthRoutes.tsx:47-89](../src/ui/src/routes/UnauthRoutes.tsx#L47-L89) — auto-login branch; Amplify `<Authenticator>` fallback |
| Session + temp creds | `src/ui/src/hooks/use-current-session-creds.ts` | [use-current-session-creds.ts:23-33](../src/ui/src/hooks/use-current-session-creds.ts#L23-L33) — `fetchAuthSession()`, 15-min refresh |
| Cognito User Pool | `template.yaml` | [template.yaml:5783-5860](../template.yaml#L5783-L5860) — password policy, email verify, triggers |
| Cognito User Pool Client | `template.yaml` | [template.yaml:5861-5916](../template.yaml#L5861-L5916) — SRP + refresh flows, 1h token, 30d refresh, no secret |
| Cognito Groups | `template.yaml` | [template.yaml:6373-6409](../template.yaml#L6373-L6409) — Admin/Author/Reviewer/Viewer + admin attachment |
| Identity Pool + Auth Role | `template.yaml` | [template.yaml:6268-6347](../template.yaml#L6268-L6347) — `AllowUnauthenticatedIdentities: false`; S3/KMS/AppSync grants |
| External IdP group mapping | `template.yaml` | [template.yaml:6047-6180](../template.yaml#L6047-L6180) — PreTokenGeneration Lambda config |

---

## 2. Authorization — using the ID token on every backend call

Once authenticated, every backend call reuses the same ID token (plus temp AWS creds for direct S3). This diagram shows three representative call paths: a GraphQL query, a mutation that results in a direct-to-S3 upload, and a DynamoDB-backed resolver that applies RBAC filtering.

```mermaid
sequenceDiagram
    autonumber
    participant Browser
    participant TopNav as GenAIIDPTopNavigation.tsx
    participant Role as use-user-role.ts
    participant Hook as use-graphql-api.ts
    participant Upload as UploadDocumentPanel.tsx
    participant Amplify as AWS Amplify<br/>generateClient()
    participant AppSync as AppSync GraphQL API<br/>(AMAZON_COGNITO_USER_POOLS<br/>+ AWS_IAM)
    participant VTL as Direct VTL resolver<br/>(DynamoDB)
    participant LambdaR as Lambda resolver<br/>list_documents_gsi_resolver<br/>/index.py
    participant UploadR as Lambda resolver<br/>uploadDocument<br/>(presigned POST)
    participant DDB as DynamoDB<br/>TrackingTable
    participant S3 as S3 Input Bucket

    Note over Browser,S3: Phase A — UI derives role, renders role-gated chrome

    Browser->>TopNav: Render top nav
    TopNav->>Role: useUserRole()<br/>(GenAIIDPTopNavigation.tsx:62)
    Role->>Amplify: fetchAuthSession()<br/>(use-user-role.ts:50)
    Amplify-->>Role: idToken.payload
    Role->>Role: groups = payload['cognito:groups']<br/>(use-user-role.ts:51)
    alt Federated user with no app groups yet
        Role->>Amplify: fetchAuthSession({forceRefresh:true})<br/>(use-user-role.ts:61)
        Note right of Role: Forces PreTokenGeneration<br/>to run and re-issue token<br/>with mapped groups
        Amplify-->>Role: refreshed idToken
    end
    alt Non-Admin → query allowed config scope
        Role->>Amplify: client.graphql({ query: getMyProfile })<br/>(use-user-role.ts:75)
        Amplify->>AppSync: POST /graphql<br/>Authorization: <idToken>
        AppSync-->>Role: { allowedConfigVersions: [...] | null }
    end
    Role-->>TopNav: { isAdmin, isAuthor, isReviewer, isViewer,<br/>canWrite, canManageUsers, canDeleteConfig, canReview,<br/>allowedConfigVersions }<br/>(use-user-role.ts:109-123)
    TopNav->>Browser: Render `${userId} (${roleDisplay})`<br/>+ Badge colored by role<br/>(GenAIIDPTopNavigation.tsx:77,93)
    Note right of TopNav: Other components hide delete/<br/>admin buttons based on these flags

    Note over Browser,S3: Phase B — GraphQL query (authenticated + RBAC-filtered)

    Browser->>Hook: listDocuments(dateRange)
    Hook->>Amplify: client.graphql({query: listDocuments, variables})<br/>(use-graphql-api.ts:22 — generateClient())
    Amplify->>Amplify: Attach header<br/>Authorization: <idToken JWT>
    Amplify->>AppSync: POST https://<api-id>.appsync-api.<region>.amazonaws.com/graphql
    Note right of AppSync: AuthenticationType:<br/>AMAZON_COGNITO_USER_POOLS<br/>(template.yaml:6515)<br/>AppSync verifies JWT signature<br/>against User Pool JWKS +<br/>checks aud==UserPoolClientId<br/>and exp.<br/>Schema type carries the<br/>aws_cognito_user_pools directive.

    AppSync->>LambdaR: Invoke resolver with<br/>event.identity.claims = {<br/>  sub, email, cognito:username,<br/>  cognito:groups: [...]<br/>}
    LambdaR->>LambdaR: caller = _get_caller_identity(event)<br/>(index.py:57-78)
    LambdaR->>LambdaR: reviewer_only = _is_reviewer_only(caller)<br/>(index.py:81-83)
    LambdaR->>LambdaR: versions = _get_user_allowed_config_versions(email)<br/>(index.py:86-122)<br/>(cached in UsersTable EmailIndex)
    LambdaR->>DDB: Query TypeDateIndex<br/>KeyCondition: ItemType='document'<br/>+ InitialEventTime BETWEEN range
    alt caller is Reviewer-only
        LambdaR->>LambdaR: Add FilterExpression:<br/>HITLTriggered=true AND<br/>(unassigned OR HITLReviewOwner=me<br/>OR HITLCompleted by me)<br/>(index.py:203-240)
    else caller has config-version scope
        LambdaR->>LambdaR: Add FilterExpression on<br/>ConfigVersion ∈ versions
    end
    DDB-->>LambdaR: Filtered items + LastEvaluatedKey
    LambdaR-->>AppSync: { items, nextToken }
    AppSync-->>Amplify: GraphQL response
    Amplify-->>Hook: documents[]

    Note over Browser,S3: Phase C — getDocument via direct VTL resolver (no Lambda)

    Browser->>Hook: getDocumentDetailsFromIds([objectKey])
    Hook->>Amplify: client.graphql({query: getDocument, variables})
    Amplify->>AppSync: POST /graphql (idToken)
    Note right of AppSync: Direct DynamoDB VTL resolver<br/>nested/appsync/template.yaml:3678-3696<br/>VTL may reference ctx.identity.claims<br/>for conditional auth. Read path here<br/>relies on the schema-level<br/>aws_cognito_user_pools gate.
    AppSync->>VTL: GetItem PK=doc#<objectKey>, SK=none
    VTL->>DDB: GetItem
    DDB-->>VTL: item
    VTL-->>AppSync: $util.toJson($ctx.result)
    AppSync-->>Amplify: { data: { getDocument: {...} } }

    Note over Browser,S3: Phase D — Upload document (mutation → presigned POST → direct PUT)

    Browser->>Upload: click "Upload"
    Upload->>Amplify: client.graphql({ query: uploadDocument,<br/>variables: {fileName, contentType, prefix,<br/>bucket: InputBucket, version} })<br/>(UploadDocumentPanel.tsx:107-116)
    Amplify->>AppSync: POST /graphql (idToken)
    AppSync->>UploadR: Invoke resolver with<br/>event.identity.claims
    Note right of UploadR: Resolver enforces<br/>canWrite (Admin or Author) before<br/>generating presigned POST.<br/>Viewer/Reviewer → Unauthorized
    UploadR->>UploadR: s3.generate_presigned_post(<br/>Bucket=InputBucket, Key=<prefix>/<fileName>,<br/>Conditions=[content-type, size])
    UploadR-->>AppSync: { presignedUrl (JSON-encoded POST data),<br/>objectKey, usePostMethod:"true" }
    AppSync-->>Upload: response
    Upload->>Upload: JSON.parse(presignedUrl)<br/>(UploadDocumentPanel.tsx:128)
    Upload->>Upload: Build FormData with<br/>{policy, x-amz-signature, x-amz-date,<br/>x-amz-credential, key, ...} + file<br/>(UploadDocumentPanel.tsx:134-142)
    Upload->>S3: POST https://<InputBucket>.s3.<region>.amazonaws.com/<br/>(UploadDocumentPanel.tsx:145)
    Note right of S3: Browser → S3 directly.<br/>S3 validates SigV4 signature<br/>from the presigned POST policy<br/>(signed server-side by resolver's<br/>IAM role — not by the user's<br/>Identity Pool credentials).<br/>Input bucket is KMS-encrypted.
    S3-->>Upload: 204 No Content
    Upload->>Browser: status: "success"

    Note over Browser,S3: Phase E — Reading S3 objects from the UI (e.g. preview images)
    Note over Browser,S3: Uses temp Identity Pool credentials<br/>(CognitoAuthorizedRole) to sign<br/>GET requests via S3RequestPresigner.<br/>See generate-s3-presigned-url.ts:46-133,<br/>role permissions at<br/>template.yaml:6306-6320.
```

### Two ways AppSync validates a request

| Auth mode | Token the browser sends | Where it's used | Reference |
|---|---|---|---|
| `AMAZON_COGNITO_USER_POOLS` (primary) | `Authorization: <Cognito ID token JWT>` | Every UI → AppSync call. AppSync verifies JWT signature against the User Pool JWKS, checks `aud` and `exp`, and populates `$ctx.identity.claims` / `event.identity.claims` (with `cognito:groups`, `sub`, `email`) for the resolver. | [template.yaml:6515](../template.yaml#L6515) |
| `AWS_IAM` (additional) | SigV4 signature using temp creds | Server-to-server paths and any UI path that needs IAM-level auth (some subscriptions / Lambda callers). Grants on the UI side come from `CognitoAuthorizedRole`'s `appsync:GraphQL` statement. | [template.yaml:6520-6521](../template.yaml#L6520-L6521), role at [template.yaml:6338-6347](../template.yaml#L6338-L6347) |

Types the UI reads/writes carry one or both of these directives in `nested/appsync/src/api/schema.graphql` (e.g. `type Document ... @aws_cognito_user_pools @aws_iam`). If a type is missing the directive a caller uses, AppSync rejects the request before any resolver runs.

---

## 3. RBAC — how Cognito groups gate behavior

Groups are defined **once** in Cognito (`Admin`, `Author`, `Reviewer`, `Viewer` — [template.yaml:6373-6409](../template.yaml#L6373-L6409)) and enforced **twice**:

### Client-side (UI chrome, convenience — not security)

`useUserRole()` reads `cognito:groups` from the ID token and exposes derived flags:

| Flag | Definition | Used for |
|---|---|---|
| `isAdmin` / `isAuthor` / `isReviewer` / `isViewer` | `groups.includes(...)` | Role badge in top nav |
| `canWrite` | `isAdmin \|\| isAuthor` | Enable Upload, Edit, Reprocess, Abort |
| `canManageUsers` | `isAdmin` | Show Users admin page |
| `canDeleteConfig` | `isAdmin` | Show destructive config actions |
| `canReview` | `isAdmin \|\| isReviewer` | Show HITL review queue |
| `allowedConfigVersions` | From `getMyProfile` (null = unrestricted) | Filter config pickers for non-Admins |

[use-user-role.ts:96-123](../src/ui/src/hooks/use-user-role.ts#L96-L123)

### Server-side (authoritative)

Every Lambda resolver re-derives the caller from `event.identity.claims` and does **not** trust anything the UI sends:

```python
def _get_caller_identity(event):
    claims = event.get("identity", {}).get("claims", {})
    groups = claims.get("cognito:groups", [])
    ...
    return { "groups": groups, "is_admin": "Admin" in groups,
             "is_author": "Author" in groups, "is_reviewer": "Reviewer" in groups,
             "is_viewer": "Viewer" in groups, ... }
```

Example — the list-documents resolver at [nested/appsync/src/lambda/list_documents_gsi_resolver/index.py:57-241](../nested/appsync/src/lambda/list_documents_gsi_resolver/index.py#L57-L241):

1. `_get_caller_identity(event)` at [:57](../nested/appsync/src/lambda/list_documents_gsi_resolver/index.py#L57).
2. `_is_reviewer_only(caller)` at [:81](../nested/appsync/src/lambda/list_documents_gsi_resolver/index.py#L81) — reviewer-only users see only HITL-pending documents that are unassigned, assigned to them, or already completed by them.
3. `_get_user_allowed_config_versions(email)` at [:86-122](../nested/appsync/src/lambda/list_documents_gsi_resolver/index.py#L86-L122) — scope non-Admins to specific configuration versions (cached via `UsersTable.EmailIndex`).
4. DynamoDB `Query TypeDateIndex` with `FilterExpression` composed from those flags — the user never sees items the filter excludes.

Mutations (delete, reprocess, uploadDocument, user management) apply the same pattern but reject outright when `canWrite`/`canManageUsers` is false.

---

## 4. Sign-out

```mermaid
sequenceDiagram
    autonumber
    participant Browser
    participant TopNav as GenAIIDPTopNavigation.tsx
    participant Amplify as AWS Amplify
    participant Cognito as Cognito Hosted UI

    Browser->>TopNav: Click "Sign Out" → confirm
    TopNav->>TopNav: sessionStorage.setItem(<br/>'idp_signed_out', 'true')<br/>(GenAIIDPTopNavigation.tsx:22)
    Note right of TopNav: Prevents auto-login from<br/>immediately re-federating<br/>(UnauthRoutes.tsx:50,53)
    TopNav->>Amplify: await signOut()<br/>(GenAIIDPTopNavigation.tsx:28)
    Amplify->>Amplify: Clear tokens from localStorage
    alt OAuth/federated (oauth block present in aws-exports.js)
        Amplify->>Cognito: 302 → {domain}/logout<br/>?client_id=<clientId><br/>&logout_uri=<redirectSignOut>
        Cognito-->>Browser: Clear Cognito session cookies<br/>Redirect → redirectSignOut
    end
    TopNav->>Browser: window.location.reload()<br/>(GenAIIDPTopNavigation.tsx:30)
    Browser->>Browser: authStatus flips to 'unauthenticated'<br/>→ Routes.tsx:42 renders UnauthRoutes
```

---

## 5. Summary of tokens and credentials in play

| Credential | Issuer | Stored where | Lifetime | Used for |
|---|---|---|---|---|
| **ID token** (JWT) | Cognito User Pool | Amplify localStorage (`CognitoIdentityServiceProvider.<clientId>.<sub>.idToken`) | 1 h ([template.yaml:5900](../template.yaml#L5900)) | `Authorization` header on every AppSync call; source of `cognito:groups`, `email`, `sub` |
| **Access token** (JWT) | Cognito User Pool | localStorage | 1 h ([template.yaml:5894](../template.yaml#L5894)) | OAuth-scoped APIs (not used by the IDP resolvers) |
| **Refresh token** | Cognito User Pool | localStorage | 30 d ([template.yaml:5910](../template.yaml#L5910)) | Silent re-issue of id/access tokens |
| **Temp AWS credentials** | Cognito Identity Pool → STS AssumeRoleWithWebIdentity → `CognitoAuthorizedRole` | In-memory (`useCurrentSessionCreds`) | ~1 h (STS default); refreshed every 15 min ([use-current-session-creds.ts:9](../src/ui/src/hooks/use-current-session-creds.ts#L9)) | Browser-side SigV4 for direct S3 GETs, KMS decrypt, SSM param read, IAM-auth AppSync calls ([template.yaml:6306-6347](../template.yaml#L6306-L6347)) |
| **Presigned POST (per-upload)** | Server-side Lambda resolver signing with its own IAM role | In the `uploadDocument` response, used once | Short (Conditions expiry) | Browser → S3 direct upload of input documents ([UploadDocumentPanel.tsx:107-158](../src/ui/src/components/upload-document/UploadDocumentPanel.tsx#L107-L158)) |

---

## 6. Key source references

### Frontend — authentication & session
- [App.tsx:72](../src/ui/src/App.tsx#L72) — `<Authenticator.Provider>` boundary
- [Routes.tsx:42](../src/ui/src/routes/Routes.tsx#L42) — auth gate
- [UnauthRoutes.tsx:47-89](../src/ui/src/routes/UnauthRoutes.tsx#L47-L89) — auto-login + Authenticator fallback
- [aws-exports.js:8-51](../src/ui/src/aws-exports.js#L8-L51) — Amplify configuration from CodeBuild env vars
- [use-current-session-creds.ts:23-62](../src/ui/src/hooks/use-current-session-creds.ts#L23-L62) — `fetchAuthSession` + 15-min refresh
- [use-user-role.ts:46-94](../src/ui/src/hooks/use-user-role.ts#L46-L94) — group extraction + federated forceRefresh

### Frontend — backend calls
- [use-graphql-api.ts:22](../src/ui/src/hooks/use-graphql-api.ts#L22) — `generateClient()` (Amplify attaches ID token)
- [UploadDocumentPanel.tsx:107-158](../src/ui/src/components/upload-document/UploadDocumentPanel.tsx#L107-L158) — mutation → presigned POST → direct S3 upload
- [generate-s3-presigned-url.ts:46-133](../src/ui/src/components/common/generate-s3-presigned-url.ts#L46-L133) — SigV4 presign using Identity Pool creds (read path)
- [GenAIIDPTopNavigation.tsx:19-34](../src/ui/src/components/genai-idp-top-navigation/GenAIIDPTopNavigation.tsx#L19-L34) — sign-out

### Backend — AuthN/AuthZ infrastructure
- [template.yaml:5783-5860](../template.yaml#L5783-L5860) — Cognito User Pool
- [template.yaml:5861-5916](../template.yaml#L5861-L5916) — User Pool Client (auth flows, token lifetimes)
- [template.yaml:6262-6265](../template.yaml#L6262-L6265) — Cognito Domain (Hosted UI)
- [template.yaml:6268-6347](../template.yaml#L6268-L6347) — Identity Pool + `CognitoAuthorizedRole` grants
- [template.yaml:6373-6409](../template.yaml#L6373-L6409) — RBAC groups
- [template.yaml:6047-6180](../template.yaml#L6047-L6180) — External-IdP group mapping (PreTokenGeneration)
- [template.yaml:6510-6528](../template.yaml#L6510-L6528) — AppSync GraphQLApi (`AMAZON_COGNITO_USER_POOLS` + `AWS_IAM`)

### Backend — authorization in resolvers
- [nested/appsync/src/api/schema.graphql](../nested/appsync/src/api/schema.graphql) — `@aws_cognito_user_pools` / `@aws_iam` directives on types
- [list_documents_gsi_resolver/index.py:57-241](../nested/appsync/src/lambda/list_documents_gsi_resolver/index.py#L57-L241) — canonical RBAC pattern: extract claims → derive flags → apply DDB filter
