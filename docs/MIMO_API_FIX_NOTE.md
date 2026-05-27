# MiMo API Fix Note

Official Xiaomi MiMo Open Platform examples use:

- Base URL: `https://api.xiaomimimo.com/v1`
- Chat endpoint: `/chat/completions`
- Auth header: `api-key: <YOUR_MIMO_API_KEY>`
- Model: `mimo-v2.5-pro`
- Completion token field: `max_completion_tokens`

If you see `HTTP 401 invalid_key`, check that:

1. `MIMO_API_KEY` is the real API key from **Console → API Keys**.
2. You did not include `Bearer ` before the key.
3. PowerShell uses single quotes if the key contains special characters.
4. `MIMO_BASE_URL` is set to `https://api.xiaomimimo.com/v1`.
5. The request header is `api-key`, not `Authorization: Bearer`.
