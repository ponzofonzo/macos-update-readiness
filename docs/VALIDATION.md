# Real-Mac validation checklist

Automated tests use **synthetic** output. Confirm these items on physical Macs before you rely on
the verdict. Turn anonymized real output into new fixtures, with serials, hostnames, battery IDs,
and URLs removed.

Suggested coverage: Apple silicon laptop (MDM-enrolled and unenrolled), Intel Mac, a Mac with
pending updates, a Mac with none, and a Mac with MDM deferrals configured.

```bash
python3 -m update_readiness --json /tmp/ur.json --html /tmp/ur.html
sudo python3 -m update_readiness --check bootstrap_token
```

| Check | Confirm on a real Mac |
|-------|-----------------------|
| `available_updates` | The parsed list matches `softwareupdate --list`. Also check: "No new software available" → `pass`; Wi-Fi off → `error` with a readable message; a major upgrade, when offered, sets `major_upgrade: true`. Nothing gets downloaded (watch `/Library/Updates`). |
| `disk_space` | Free GB is close to Disk Utility "Available". |
| `power` | Unplugged laptop below 50 % → `fail`. Plugged in → `pass`. Desktop → `pass`. |
| `bootstrap_token` | Without sudo → `skip`. With sudo, it matches `sudo profiles status -type bootstraptoken`. |
| `update_settings` | Values match System Settings > General > Software Update > Automatic Updates and any Jamf Software Update payload. `plutil` reads the binary plist. |
| `deferrals` | With a Restrictions payload that sets deferral days, the days match the payload. |
| General | Run time with and without `--skip-catalog`. `softwareupdate` can take several minutes. |
