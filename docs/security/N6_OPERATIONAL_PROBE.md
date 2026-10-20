# Operational Bedrock probe authority (N6)

`bedrock_health.probe()` preserves `{configured, reachable, failure?}` and the
configured-but-unreachable deep-health 503 rule. Success proves only acceptance
of a synthetic streaming invocation by the configured Bedrock client/model.
It does not prove user consent, user feature availability, provider destinations,
or readiness of every blocking/streaming feature.

The sole operational operation is `ai_provider_call.probe_bedrock_readiness()`.
It accepts no arguments. The payload is code-owned `ping`, one output token,
no tools, files, images, history, user identity or caller-selected recipient.
It uses the existing configured Bedrock streaming client (SDK retries disabled),
a five-second transport timeout and no application retry or OpenAI fallback.
The existing asymmetric health cache is retained. The timeout is the SDK's
transport timeout, not a hard wall-clock cancellation guarantee.

This authority is outside the user-consent domain; it does not create a grant
or policy, and does not add health_probe to BINDINGS. Generic admit() remains
closed for health_probe, regardless of gate/charge options. User create/stream
admissions and their final D1 checks are unchanged. No endpoint exposes this
operation with arbitrary inputs. The internal-network deep-health restriction
is unchanged. The operation emits bounded usage metadata with subject_id=None.

Threat boundary: application code is trusted; this is not a sandbox against
malicious Python code with process access. Any new caller or parameter is a
security contract change requiring review. Do not turn this into a generic
skip-consent flag. Privacy disclosure publication and recipient policy remain
separate founder/Legal decisions. No configuration/IAM/provider change is made.
