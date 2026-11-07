## Summary

<!-- What changed and why? Link the issue it closes, if any. -->

## Verification

Tick what actually ran. Every command below is a real target in this repo; if a
line does not apply to your change, say so rather than leaving it unchecked.

- [ ] Python suite: `.\.venv\Scripts\python.exe -m unittest discover -s tests -t .`
- [ ] Daemon builds: `dotnet build .\src\StatusCast.Daemon\StatusCast.Daemon.csproj -c Release`
- [ ] Daemon tests: `dotnet run --project .\tests-csharp\StatusCast.Daemon.Tests\StatusCast.Daemon.Tests.csproj -c Release`
- [ ] Firmware host tests (only if `firmware/esp32/` changed): `.\firmware\esp32\tools\host-test.ps1`
- [ ] Firmware builds (only if `firmware/esp32/main/` or `firmware/esp32/CMakeLists.txt` changed): `.\firmware\esp32\tools\build.bat build`

## Compatibility

StatusCast has three consumers of the same contract: this daemon, the ESP32
firmware, and NekoTool. Anything that changes a key, a field order, a status
code or an error body is a breaking change for the other two.

- [ ] Not a contract change, **or** `schema/status-contract.json` is updated in this PR
- [ ] If `/status/` or `/health` output changed: the ESP32 side is described in the PR body

## Hygiene

- [ ] No `run/` output, snapshots or logs are committed
- [ ] No credentials, tokens, real device MAC addresses, LAN addresses or SSIDs are committed
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]` for user-visible changes

## Notes

<!-- Known limitations, screenshots, follow-up work. -->
