# ADR 0006: Role accounts, personal access tokens and a role matrix

Status: Accepted. Date: 2026-10-15. Decision round 43; closes second-look items 1 and 10.

## Decision
- **Users are role accounts** seeded from the organisation: the risk function (CRO, Head of
  Market Risk, Head of Counterparty Risk, Risk Control), a head for each business and desk, any
  other firm-level limit owner (the fund's CFO), each sign-off role a run metric names (Head of
  Product Control, Head of Model Validation, ...), and a read-only Viewer. A user's name is the
  role string the workflows already record, so the audit trail and the approval matrix keep
  their names. `novera simulate` seeds them; `novera users seed` refreshes an existing database.
- **Personal access tokens**: `novera users token <user>` prints a random token once. The
  database keeps only its SHA-256 hash, a recognisable prefix, an expiry (90 days by default)
  and a revocation time (`novera users revoke`).
- **The API takes the actor from the token**, never from the request body. A write without a
  valid token is `401 UNAUTHENTICATED`. A role or scope that does not allow the action is
  `403 FORBIDDEN`, with the user, action and limit in the problem's context. Reads stay open on
  the local API unless `NOVERA_AUTH_REQUIRED_FOR_READS` is set.
- **The role matrix** (`novera/security/permissions.py`), enforced in the write service, so
  both clients and the CLI behave alike:

  | Action | Who |
  |---|---|
  | Acknowledge a breach, request an increase | The limit's owner, the head of its desk or business, or the head of risk over it |
  | Comment on a breach | The same, or Risk Control |
  | Escalate or close a breach | Risk Control, or the head of risk over the limit |
  | Decide an increase or a limit draft | Heads of risk, then the existing approval matrix by level and size; never the requester |
  | Run the EOD, re-run a stage, run the Lab | Risk Control, Head of Market Risk, CRO |
  | VaR setup, sign-off policy, propose a recalibration | Head of Market Risk, CRO |
  | Sign or reject a run metric | The signer the metric names (OPS-003), or the CRO |
  | Run an agent | Risk Control and the heads of risk |
  | Approve or reject a CSA proposal | Head of Counterparty Risk, CRO |
  | Ask the Analyst, build a risk pack | Any signed-in user |

  "Head of risk over the limit" is the Head of Counterparty Risk for counterparty limits, the
  Head of Market Risk for every other limit, and the CRO for all. A limit's desk comes from its
  scope (desk, book, trader or business), not from the owner's name.
- **The dashboard's "Acting as" picker** stands in for the firm's single sign-on. In process,
  the dashboard calls the same checked service as the user picked. Over HTTP
  (`NOVERA_API_URL`), it sends `NOVERA_API_TOKEN` and shows who that token belongs to.
- **The CLI is the operator console.** It runs on the database host with file access, so
  `--actor` is trusted to say who is acting, but only as a known user, whose role then decides
  what the command may do. Every audit event it writes carries `via: {channel: cli, os_user}`.
- **MCP over streamable-http** verifies the same tokens. Every tool call is audited under the
  token's user, and the agent tool needs an agent-running role as well as `--allow-agents`.
  Over stdio, the local operator is trusted as before.

## Why
The approval matrix, four-eyes rule and named signers were real rules that were enforced on
strings anyone could type. A bank's security review would raise this first: the governance
story is only as good as the identity behind it.

Role accounts keep the change inside what the workflows already record. Hashed tokens with
expiry and revocation behave like the personal access tokens a firm's gateway would issue, so
replacing them with single sign-on later changes `current_user`, not the matrix.

## Not done
- No real single sign-on (SAML, OIDC), password, MFA or session handling: the picker and the
  tokens stand in for them.
- One person per role: a person who holds two roles, or a role held by two people, is not
  modelled.
- The CLI is not authenticated: anyone with the database file can claim a user.
- The API serves plain HTTP; TLS belongs to the firm's gateway.
