# Security Policy

CompatD installs packages and runs commands as root through `pkexec`, so security problems matter. Thank you for reporting them responsibly.

## Supported versions

CompatD is a pre-release. Only the latest version on `main` gets security fixes.

| Version | Supported |
|---|---|
| 0.1.x (latest) | Yes |
| older | No |

## Reporting a vulnerability

**Please don't open a public issue.** Instead, use one of these:

* GitHub's private reporting: the **Security** tab of this repository → **Report a vulnerability**.
* Email **unforged.crl@gmail.com** with "CompatD security" in the subject.

Include:

* the CompatD version and your distribution (`compatd info`)
* what an attacker can do, and what they need first (a local account, a crafted file to scan, a malicious package name, …)
* steps to reproduce, or a proof of concept

You should get a reply within 7 days. Once the problem is confirmed, a fix will be prepared and released, and you'll be credited in the changelog unless you'd rather not be.

## What counts

Examples of what to report:

* a way to run commands as root without the confirmation dialog, or different from the commands it shows
* shell injection through file names, package names, apt sources or other data CompatD reads
* the scanner executing or loading a file it inspects
* an automatic fix that deletes or changes files outside what it describes

Wrong recommendations and false positives are ordinary bugs. Report them as issues.
