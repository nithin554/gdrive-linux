# Security Policy

## Supported Versions

We release patches for security vulnerabilities in the latest stable release only.

| Version | Supported |
|---------|-----------|
| Latest release | ✅ |
| Older releases | ❌ |

## Reporting a Vulnerability

**Do not open public issues for security vulnerabilities.**

If you discover a security vulnerability, please report it privately by emailing **nithinneeraj60@gmail.com**.

Please include the following details in your report:

- Type of vulnerability
- Steps to reproduce
- Affected version(s)
- Any potential impact

You can expect:

- **Acknowledgment** within 48 hours of your report
- **Updates** every 7 days on the status of the fix
- **Credit** in the release notes (if desired) once the fix is published

## Scope

This security policy covers:

- The gdrive-linux application and its source code
- OAuth credential handling
- File system operations (FUSE)
- Network communication with Google Drive API

Out of scope:

- Google Drive API itself
- Third-party dependencies (report vulnerabilities to their respective maintainers)
- Operating system security (FUSE kernel module, etc.)

## Best Practices

Users are encouraged to:

1. **Only use OAuth credentials from a trusted Google Cloud project** — do not share your `client_id`/`client_secret`
2. **Keep the application updated** to the latest version
3. **Review the permissions** requested by the application (scopes are listed in `config.py`)
4. **Do not run as root** — the application runs correctly as a normal user
