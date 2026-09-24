# A1: Portable runs and reproducibility basics

Repo: slolab/agent-harness-lab · Needs: A0 · Roadmap decisions: 3, 13, 15 ([roadmap](https://github.com/slolab/biotope-bench/blob/main/docs/roadmap.md))

## Goal and why

AHL becomes usable as a pinned dependency of another repository, such as biotope-bench. Today it assumes the config, `.env` and `runs/` sit in its own checkout, builds images from that checkout, and installs the latest harness versions. Afterwards the config, env file and run directory can live anywhere, images build from the installed package with pinned harness versions, and every run records which AHL and which image produced it, so results can be traced and repeated. Four generic config keys cover what callers need next: data mounts, an existing Docker network, non-secret environment variables and package extras.

## Scope

- Path resolution for a config outside the checkout; `--env-file` and `--runs-dir`.
- Dockerfiles shipped with the package; an `ahl build` command.
- Pinned Claude Code, OpenCode and uv versions; image labels; provenance in `session.json`.
- Config keys `mounts`, `network`, `env` and `packages[].extras`.

## Non-goals

- Headless execution (A2) and the Codex harness (A3).
- Pinning Gemini, agy and Claude Science. DeepSeek is already pinned by its lockfile.
- Egress restriction, non-root containers, resource limits, compose files.

## Interfaces

- **Paths.** Command-line paths (`-c`, `--env-file`, `--runs-dir`, and A2's `--turn`) resolve against the working directory. Paths inside the config (`workspace`, `capabilities[].path`, `packages[].path`, `mounts[].path`) resolve against the config file's directory.
- **Env file.** `--env-file PATH` if given, otherwise `<config dir>/.env` if it exists. An explicit `--env-file` wins over shell variables for the keys it defines, and a path that does not exist is a config error. The implicit `<config dir>/.env` loses to the shell, as today. A missing provider key produces an error naming the env file read, or saying none was read.
- **Runs dir.** `ahl up --runs-dir DIR` puts the run at `DIR/<run id>`, and `--name` and `--resume` resolve inside `DIR`. The default stays `<config dir>/runs`.
- **Build.** `ahl build (--harness NAME | -c CONFIG)` builds `agent-harness-lab:<harness>` from the installed package. It needs no provider key and reads no env file; with `-c` it reads only `harness`. `ahl up` builds by default in the same way, and `--no-build` skips it.
- **Image labels.** Every image carries `ahl.harness=<name>`. The pinned images (claude, opencode, deepseek) also carry `ahl.harness.version=<version>`; gemini, agy and claude-science do not.
- **`session.json`** keeps its fields and gains `ahl: {version, git_sha, git_dirty}` and `image: {name, id, harness_version}`, e.g. `{"name": "agent-harness-lab:opencode", "id": "sha256:…", "harness_version": "1.18.32"}`. `git_sha` and `git_dirty` are null outside a git work tree. `image` describes the image the container started from, also with `--no-build`. `harness_version` is null when the image has no version label.
- **Config keys.** Every error below is a config error naming the entry or variable, raised before any container starts.
  ```yaml
  mounts:                                   # extra bind mounts
    - path: ../datasets/YAGO2geo/unpacked   # must exist
      target: /workspace/data               # absolute, unique, not /workspace itself
      readonly: true                        # optional, default true
  network: my-compose-project_default       # optional; an existing Docker network to join
  env:                                      # optional; non-secret variables, string values
    NEO4J_URI: bolt://neo4j:7687
  packages:
    - {name: mypkg, install: mount, path: ../mypkg, extras: [graph]}   # extras optional
  ```
  - `env` rejects names AHL manages: every provider key variable, and every variable the selected harness adapter sets (from A2, its headless environment too).
  - `extras` works in both install modes. For a package with a console script, the extras land in the tool's own environment, not in the system `python3`.

## Acceptance criteria

- **AC-1** (unit) With a config outside the AHL checkout and an unrelated working directory, config paths resolve against the config directory, and relative `-c`, `--env-file` and `--runs-dir` paths against the working directory. `--runs-dir DIR` creates the run at `DIR/<run id>` and resolves `--name` and `--resume` there.
- **AC-2** (unit) `--env-file` loads its file and wins over a conflicting shell variable; a missing `--env-file` path is a config error naming it. Without the flag, `<config dir>/.env` is loaded and the shell wins. The missing-key error names the env file read, or says none was read.
- **AC-3** (unit, docker) From an unrelated working directory, `ahl build -c CONFIG` with no provider key and no env file builds the configured harness from the package's image files (unit, Docker stubbed). Run outside the AHL checkout, `ahl build --harness claude` and `ahl build --harness opencode` build both images (docker).
- **AC-4** (unit, docker) No Dockerfile defaults a harness or uv version to `latest` or another moving tag. Every image's labels follow the Interfaces, and `docker image inspect` on the claude and opencode images shows `ahl.harness.version` equal to the installed harness version.
- **AC-5** (unit, docker) `session.json` carries the `ahl` and `image` fields, with `image.harness_version: null` for an image without the version label. In the docker tier, `image.id` and `image.harness_version` match `docker image inspect`, with and without `--no-build`.
- **AC-6** (unit) Each `mounts` entry becomes a bind mount, read-only unless `readonly: false`. A missing source, a relative target, the target `/workspace` and duplicate targets are each rejected.
- **AC-7** (unit) `network` joins the named network, and a network that does not exist is rejected. `env` sets each variable in the container. A non-string value, `OPENROUTER_API_KEY`, and `ANTHROPIC_BASE_URL` for `harness: claude` with `provider: openrouter` are each rejected.
- **AC-8** (unit, docker) `extras` reaches the install for packages with and without console scripts, in both install modes (unit). A fixture package without console scripts, whose extra pulls in one PyPI dependency, installs so that the container's `python3` imports that dependency (docker).
- **AC-9** (unit) Existing use keeps working: run from the checkout with `./config.yaml`, `ahl up` reads `./.env` and writes to `./runs/`. Existing tests pass, changed only where they referenced the location of the image files.
- **AC-10** (unit) `config.example.yaml` demonstrates `mounts`, `network`, `env` and `extras`, and loads through the real config loader with paths resolved against its own directory.

## Freedom to operate

Where the image files live in the package and how they are found; the pinned versions (current stable releases); how pins and labels are expressed in the Dockerfiles; how config errors are worded; test layout and fixtures.

## Design sketch (non-binding)

- Move `docker/` to `src/ahl/images/` and resolve it with `importlib.resources`. The name avoids a clash with `ahl.docker`.
- `load_config` gains an optional env-file argument. `docker image inspect` just before start fills `image`; `docker network inspect` checks `network`.
- Setup commands run under `sh -c`, so a requirement with extras needs shell quoting, e.g. `'/opt/ahl-packages/mypkg[graph]'`.

## Risks and open questions

- The DeepSeek image runs `npm ci` in its build context. Moving the context must keep that working, although DeepSeek is otherwise out of scope. The wheel must include the non-Python image files.

## Evidence required in the PR

- The pinned versions, `docker image inspect` label output for the claude and opencode images, and a sample `session.json`.
- The wheel listing showing the image files, the DeepSeek build result, and `uv run pytest -m docker` output.
- `README.md` and `config.example.yaml` document `--env-file`, `--runs-dir`, `ahl build`, `mounts`, `network`, `env` and `extras`.
