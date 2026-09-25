## Execution Plan: Run Forexbot M15 Ensemble Strategy

### 1. Required Configuration Files
- **`config/default.yaml`** (critical):
  - Ensure `strategy.name: "ensemble"`
  - Set `timeframe: "15m"`
  - Verify `symbols: ["EURUSD"]`
  - Example valid section:
    ```yaml
    strategy:
      name: "ensemble"
    timeframe: "15m"
    symbols: ["EURUSD"]
    ```
  - Confirm no typos (case-sensitive; `strategy.name` must exactly match the ensemble config)

### 2. Execution Command
```bash
forexbot run --config config/default.yaml
```
- **Note**: This runs in live/paper mode (no broker configuration required for smoke tests). Ctrl+C cleanly stops the loop.
- **Required**: Config must include valid symbols and broker settings (if using live trading).

### 3. Verification Steps
1. **During Execution**:
   - Monitor terminal for status logs (e.g., `mode=live`, `symbols=1`, `tf=15m`)
   - Wait ≥60 days for real-data validation (per memory)
2. **After 60 Days**:
   ```bash
   forexbot report --limit 20
   ```
   - Confirm equity curve shows **net profit > 0** (e.g., `+1.92%`) in output
   - Check for `PF 1.54` (Profit Factor) in trade summary

### 4. Error Handling
- **Missing Dependencies**:
  - Run `pip install -r requirements.txt` (if project includes it)
  - Verify Python ≥3.10 is installed
- **Config Errors**:
  - Check paths via `forexbot run --config config/default.yaml`
  - Validate keys: `strategy.name` (not `strategy.name`), `timeframe` (e.g., `15m`)
- **Broker Connectivity**:
  - Run `forexbot check` first to verify credentials
  - Ensure broker config (e.g., API keys) exists in `config/default.yaml`

### Critical Notes
- **Never modify configs without reading first**: Use `Read config/default.yaml` before edits
- **M15 ensemble requires exact config**: Only this specific config yields +1.92%/60d profit
- **60 days minimum**: Real-data validation requires extended runtime
