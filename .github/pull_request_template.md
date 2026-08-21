## Summary

<!-- What does this PR change and why? -->

## User / Product Impact

<!-- What changes for the user or product? Write "None" if this is internal only. -->

## Scope

### Included

*

### Out of scope

*

## Architecture / Authority

<!--
Does this introduce or change a source of truth, write path, ownership boundary,
API contract, background process, AI authority, or persistence authority?

If not applicable, write "No authority change."
-->

## Security & Privacy

* [ ] No authentication or authorization behavior changed
* [ ] No new sensitive data is stored or exposed
* [ ] No cross-user ownership boundary changed
* [ ] No secret, credential, token, private URL, or user data is logged
* [ ] Any security/privacy change is explained below

Details:

<!-- Required when any item above does not apply. -->

## Database / Migration

* [ ] No schema change
* [ ] Migration included
* [ ] Migration tested from the previous head
* [ ] `flask --app starter db check` passes

Migration / persistence notes:

<!-- State "None" when not applicable. -->

## Testing

### Focused

```text
<command>
<result>
```

### Full / Regression

```text
<command>
<result>
```

### Additional validation

<!-- PostgreSQL concurrency, browser matrix, compileall, static checks, etc. -->

*

## Failure / Rollback

<!-- What happens if this change fails? How is it safely reverted or disabled? -->

## Review Findings

* Critical / P0:
* Important / P1:
* Minor / P2:

## Shipping Checklist

* [ ] Scope is bounded and documented
* [ ] Tests cover the changed contract
* [ ] Existing behavior outside scope remains unchanged
* [ ] No secrets or sensitive artifacts are committed
* [ ] CI is green on the final SHA
* [ ] Migration state is valid where applicable
* [ ] Documentation is updated where required
* [ ] Rollback path is understood
