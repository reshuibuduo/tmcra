# Publishing TMCRA on GitHub

This directory is the GitHub publication root. Do not publish the parent
release-bundle directory as a repository: its packages, validation reports, and
full archive are release artifacts, not normal source history.

No GitHub repository is created or pushed by this document.

## Recommended repository settings

Create a new **empty** public GitHub repository, for example OWNER/tmcra. Do
not ask GitHub to initialize a README, license, or gitignore; this source tree
already has those files and its own clean Git history.

Suggested description:

> Self-hosted temporal memory service, clients, SDKs, MCP tooling, and benchmarks.

After the initial push:

1. Enable private vulnerability reporting.
2. Protect main: require pull requests for maintainers, block force pushes,
   and require a passing release audit before a release tag is created.
3. Enable Issues and Discussions if they will be actively moderated; otherwise
   disable them rather than leaving an unattended support channel.

## Initial push from PowerShell

Set releaseRoot to the directory containing the prepared release bundle. This
repository is already committed locally; do **not** run git init or add the
parent directory.

~~~powershell
$releaseRoot = '<release-bundle-directory>'
Set-Location -LiteralPath "$releaseRoot\source"

git status --short
git log -1 --oneline
git remote -v

# Expected before publication: no status output and no remotes.
git branch -M main
git remote add origin git@github.com:OWNER/tmcra.git
git remote -v
git push -u origin main
~~~

Use the HTTPS remote instead if that is the team's approved authentication
method. Authenticate with GitHub before pushing. The first GitHub repository
must be empty to avoid an unrelated-history merge.

## Release assets

Keep source in Git and publish distributable archives through GitHub Releases:

- tmcra-oss-staging-20260818.tar.gz
- SHA256SUMS.txt

Create the release as a **draft**, attach both files, verify the uploaded
archive checksum against SHA256SUMS.txt, then publish it after the tag, release
notes, and assets have all been reviewed. Create a tag only after the GitHub
`Release gate` workflow is green. Run the metadata checks locally before
tagging:

```bash
python scripts/check_release_versions.py
python scripts/check_release_secrets.py
```

The current candidate is `v0.3.0-rc1`. Promote it to a stable tag only after a
clean-checkout deployment and artifact-hash review. Copy the release notes from
`CHANGELOG.md`.

The full archive is intentionally not committed to this repository. GitHub
warns for ordinary Git files above 50 MiB and blocks them above 100 MiB; use a
Release asset for the current archive. Git LFS is unnecessary for the current
source tree because its largest tracked file is below 50 MiB.

## Final preflight

Before creating the public repository:

1. From the release bundle, run the release audit and require P0=0 and P1=0.
2. Confirm git status --short in this directory is empty.
3. Confirm the source_commit in the bundle status file matches
   git rev-parse --short HEAD.
4. Review SECURITY.md, NOTICE, model provenance, and the public README from a
   logged-out browser session.
5. Never add environment files, provider credentials, private keys, customer
   data, production logs, generated databases, or the parent release archive
   to Git history.
6. Require every job in `.github/workflows/release-gate.yml` to pass.
