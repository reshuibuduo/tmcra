# Required release resources

The desktop build expects these generated files in this directory:

- `tmcra-codex-latest.zip`
- `tmcra-codex-release.json`

They are release artifacts rather than handwritten source, so they are ignored
by Git. Generate them with the TMCRA plugin release script and copy the matching
pair here before running `npm run dist:win`.
