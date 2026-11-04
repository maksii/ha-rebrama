# Security

Rebrama controls physical access. Keep Home Assistant and this integration up to
date, restrict access to the configuration directory, and protect backups.
Passwords and rotating tokens stay in Home Assistant's config-entry storage.
Share URLs and slugs grant access and must be treated as credentials.

Do not put credentials, unredacted storage files, or share URLs in public issues.
For a suspected vulnerability, contact the repository owner privately through
the contact options on https://github.com/maksii before publishing details.
Download integration diagnostics for support; they redact authentication data
and omit share URLs/slugs. Debug logs include HTTP metadata only.

The integration does not report a door's physical locked/open/closed state. An
Open button sends a momentary command; successful delivery is not proof that a
door opened or later closed. Cloud/network failures never automatically repeat
an open or temporary-access mutation.
