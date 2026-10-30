# Security

## Reporting a problem

If you find a security issue, please **don't open a public issue**. Use GitHub's private [security advisory](https://github.com/elloexe/clauditing/security/advisories/new) form instead. You'll get a reply within a few days.

## How Clauditing handles your data

- **GitHub token.** Stored only on your PC, in `%APPDATA%\Clauditing\settings.json`, and only sent to `api.github.com`. It is never shown in the interface or sent to the app's own pages. A fine-grained token with read-only **Contents** access to one repository is all it needs.
- **Local server.** The app's interface talks to a small server that listens on `127.0.0.1` only, on a random port, and refuses every request that doesn't carry a random key created when the app starts. Media requests can't reach files outside your clip library.
- **Downloads.** Release downloads follow GitHub's redirect to its storage without passing your token along.
- **Your videos.** Editing happens entirely on your PC with ffmpeg. Nothing is uploaded unless you publish it yourself.
