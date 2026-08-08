# Azure OpenAI live-validation record

## Result

**Status: live-validated for one bounded synthetic recommendation on
2026-08-07.**

The optional `AzureOpenAIProvider` was exercised against an existing
`gpt-5.4-mini` deployment (model version `2026-03-17`, `DataZoneStandard`). No
subscription identifier, tenant identifier, account identity, endpoint,
credential, or API key is stored in this record.

The request used:

- one synthetic `ECONOMIC_VALUE_MISMATCH` on `/payload/base_amount`;
- retrieved synthetic runbook candidates only;
- Azure Structured Outputs;
- `reasoning_effort=minimal`;
- `max_completion_tokens=400`;
- Microsoft Entra authentication through the local Azure CLI session;
- no database connection and no action/execution tool.

The provider returned a schema-valid recommendation with:

- `recommended_action: CORRECT_LEGACY_BOOKING_FIELD`;
- proposed value `/payload/base_amount = 1018000.00`, exactly matching the
  deterministic reconciliation fact;
- `confidence: 0.98` and `priority: HIGH`;
- citations limited to existing `RB-001` sections;
- required approvals `MAKER` and `CHECKER`.

The existing deterministic policy engine independently evaluated that output
as:

```json
{
  "outcome": "ELIGIBLE_FOR_APPROVAL",
  "reasons": ["economic_field_correction_eligible"],
  "required_approvals": ["MAKER", "CHECKER"],
  "approved_field_path": "/payload/base_amount",
  "approved_value": "1018000.00"
}
```

The script exited successfully. It did not approve, sign, dispatch, or execute
the recommendation.

## Reproduction

```bash
python -m pip install -e ".[dev,azure]"
az login
export AZURE_OPENAI_ENDPOINT=https://<resource-name>.openai.azure.com
export AZURE_OPENAI_DEPLOYMENT=<deployment-name>
export AZURE_OPENAI_MAX_COMPLETION_TOKENS=400
export AZURE_OPENAI_REASONING_EFFORT=minimal
python scripts/run_azure_recommendation_demo.py
```

`AZURE_OPENAI_API_KEY` is optional; when absent, the provider uses
`DefaultAzureCredential`. Never commit either an endpoint-specific credential
or a copied key.

## Claim boundary

This record proves only that the repository's Azure adapter can obtain one
strict, citation-backed recommendation from the named model and pass it through
the existing deterministic policy gate. It does **not** prove production
deployment, operational resilience, model quality across other break families,
autonomous remediation, UiPath integration, or a live banking-system write.
