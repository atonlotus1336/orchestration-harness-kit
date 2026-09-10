# Private distribution policy

This kit is private, proprietary, and not licensed for public or unauthorized
redistribution. That restriction applies whether redistribution would be free
or paid.

## Recipient rules

Share the kit only with a recipient whom the Kit Proprietor has authorized in writing.
Use a private repository or an access-controlled archive. Do not create public
forks, mirrors, releases, package publications, container publications, or
download links. Give each recipient `LICENSE`, this policy, and
`THIRD-PARTY-NOTICES.md` with the payload.

Record the recipient, artifact digest, authorization, delivery date, and any
revocation or deletion request. Revoke repository and artifact access when the
authorization ends. A repository invitation or file transfer alone is not a
substitute for a confidentiality agreement when confidential treatment matters.

## Open-source boundary

The proprietary restriction covers the Kit Proprietor's original wrapper, integrations,
policies, modifications, documentation, and packaging. It does not replace or
restrict the licenses of incorporated or referenced open-source components.
Those components retain their original licenses and required notices; see
`THIRD-PARTY-NOTICES.md`.

Do not distribute a built image, executable, or dependency bundle until its
transitive license inventory and required notices have been reconciled. The
repository SBOMs are technical inventories, not a complete legal clearance.

## Release boundary

The internal filename `installer/contracts/public-payload.json` is a legacy
technical name for an allowlisted recipient payload. It is not permission to
publish the kit. Release tooling must default to private visibility and must
fail if the three distribution notices are absent from the recipient payload.

This policy is an operational notice, not legal advice or a replacement for a
recipient agreement reviewed by qualified counsel.
