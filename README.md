# LineCare

A support agent (chat, later voice) for **Quillnet**, a fictional broadband and TV operator. It verifies the
caller, runs remote diagnostics on the line and the router, explains outages and bills, and restarts a frozen
router only after the customer agreed. The rules that matter live in code: the identity gate, the two-step
confirmation for actions, and the redaction of PINs and dates of birth.

> **Work in progress.** Phase D1 of the plan in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) is done: mock
> enterprise systems, the customer agent's core and a minimal chat page. Knowledge base with citations, cases,
> technician booking, handover and voice come next.

## Quick start

1. Start LM Studio's local server (this project uses port 1235) with `gemma-4-26b-a4b-it` loaded.
2. Run:
   ```bash
   pip install -r requirements.txt
   uvicorn server:app --host 127.0.0.1 --port 8002
   ```
3. Open `http://localhost:8002`. The right panel lists fictional demo accounts with their PINs, one per scenario.
   The mock systems' REST API is documented at `http://localhost:8002/docs`.

## Tests

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium     # or, on Windows with Edge: add --browser-channel msedge
python -m pytest --cov=linecare --cov=mocks --cov=server
```

The tests use a scripted fake of LM Studio and a frozen clock, so they need no GPU and give the same result on
every run.

## License

[MIT](LICENSE)
