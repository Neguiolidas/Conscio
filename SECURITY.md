# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.9.x   | ✅ Active |
| < 0.9   | ❌ End of life |

## Reporting a Vulnerability

**Do not** report security vulnerabilities through public GitHub issues.

Instead, email **neguitech@proton.me** with:

1. **Description** of the vulnerability
2. **Steps to reproduce** (or proof of concept)
3. **Affected versions**
4. **Potential impact**

You should receive a response within 72 hours. If the issue is confirmed:

- A fix will be developed in a private branch
- A patch release will be published as soon as possible
- Credit will be given in the release notes (unless you prefer to remain anonymous)

## Scope

- Vulnerabilities in the Conscio Python package (`conscio/`)
- Vulnerabilities in the test suite that could leak data
- Supply chain risks in dependencies

## Out of Scope

- Issues in downstream applications using Conscio
- Vulnerabilities in optional/integration services (Ollama, OpenAI, etc.)
- Social engineering or phishing

## Best Practices

- **Never commit secrets** — API keys, tokens, or credentials must not appear in source code
- **Pin dependencies** — review `pyproject.toml` before upgrading
- **Sandbox AI connections** — Conscio connects to LLM providers; ensure API keys are in environment variables, not in code
