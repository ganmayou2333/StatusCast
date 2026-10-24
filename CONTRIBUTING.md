# Contributing to StatusCast

Thanks for your interest in improving StatusCast.

## Before opening an issue

- Check the existing issues and the Roadmap in `README.md`.
- For USB detection problems, run `.\.venv\Scripts\python.exe status_core.py` and include the output.
- For HTTP API problems, include the response status and the `API:` indicator state from the app.

## Pull requests

1. Fork the repository and create a feature branch.
2. Keep changes focused; avoid unrelated refactors in the same PR.
3. Run the existing checks before opening the PR:

```powershell
.\.venv\Scripts\python.exe -m unittest discover
dotnet build .\gui\StatusCast.Gui\StatusCast.Gui.csproj -c Debug -p:Platform=x64
```

4. Describe the user-facing effect and any known limitations.

## Code style

- Python: standard library style, avoid adding dependencies unless necessary.
- C#: nullable enabled, keep UI work on the UI thread via `DispatcherQueue`.
- Do not commit `.venv/`, `bin/`, `obj/` or `run/`.

## Security

Please do not report security-sensitive issues in public issues. Use the repository's private security advisory flow instead.
