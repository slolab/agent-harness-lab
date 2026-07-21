# Claude Science harness

Claude Science is supported as an x64 glibc Linux target. The Docker image is
also forced to `linux/amd64`, but full app, OAuth, and inner-sandbox validation
must be done on the target Linux host rather than the arm64 development Mac.

## Configuration

```yaml
harness:
  name: claude-science
  parameters:
    port: 8000
provider: anthropic
workspace: ./projects/science-demo
capabilities:
  - kind: skill
    name: my-analysis-skill
    install: copy
    path: ./skills/my-analysis-skill
```

No `.env` file or `ANTHROPIC_API_KEY` is needed. `model` has no effect for
Claude Science because the signed-in app/plan controls model availability.

Run `uv run ahl up`. In the container shell, run the printed command:

```bash
claude-science serve --no-browser --no-auto-update --host 0.0.0.0 --port 8000
```

Open the single-use `Web UI -> http://localhost:8000/?nonce=...` URL printed
by the command. Docker publishes ports 8000 and 8001 only on host loopback;
port 8001 is the separate HTML-preview origin. A custom `parameters.port`
publishes that port and the next one instead. If the printed URL names
`0.0.0.0`, replace only that hostname with `localhost` in the host browser.

Sign in with a Pro, Max, Team, or Enterprise Claude account. Team and
Enterprise organizations must enable Claude Science. If OAuth cannot redirect
back to the app, choose **Paste a code** on the sign-in screen.

Grant `/workspace` when the app requests folder access. The host-side
`runs/<id>/workspace/` copy is mounted there read-write, so input files are
visible and generated plots, notebooks, and other outputs remain run
artifacts after the container exits.

## Custom-skill fallback

Claude Science currently documents custom skill installation only through
Settings, not through a CLI or a supported on-disk import directory. AHL does
not mutate the app's private database. It packages every configured skill as
`runs/<id>/claude-science/skill-uploads/<name>.zip`; after sign-in, perform the
single fallback step printed by `ahl up`: open **Settings > Skills**, choose
**Add skill / Upload a skill**, and select each ZIP from that host path.

This means success criterion 5 (zero manual UI steps) is not met by the current
Claude Science beta. The upload is the documented one-step fallback; all other
harness setup remains automatic and per-run.

## Code-execution sandbox

The default launch keeps Claude Science's bubblewrap sandbox enabled. Docker's
seccomp and AppArmor profiles are relaxed for this container so bubblewrap can
create nested namespaces; the app still asks for folder, network, and code
execution approvals.

If nested bubblewrap remains unavailable on the target kernel, opt into the
agreed container-boundary fallback explicitly:

```yaml
harness:
  name: claude-science
  parameters:
    dangerously_no_sandbox: true
```

This appends `--dangerously-no-sandbox` and prints a warning. It gives analysis
code access to the container filesystem and network; it does not grant access
to host files beyond AHL's declared bind mounts.

## Linux acceptance checks

1. Start with no `.env` file and confirm `docker inspect <container>` contains
   no `ANTHROPIC_API_KEY`.
2. Start the printed command, open its nonce URL, and complete account login.
3. Approve `/workspace`, analyze a seeded CSV, request a Python-generated plot,
   and verify it appears under `runs/<id>/workspace/` on the host.
4. Invoke each uploaded custom skill.
5. Start a second fresh named run and confirm it has no projects, credentials,
   artifacts, or conversation history from the first run.

The first launch downloads starter Python and R environments and needs roughly
5 GB of disk space. Consecutive fresh runs intentionally repeat account login
and do not share `~/.claude-science`.
