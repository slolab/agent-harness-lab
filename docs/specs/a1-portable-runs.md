# A1: Portable runs and reproducibility basics

Repo: slolab/agent-harness-lab · Needs: A0 · Roadmap decisions: 3, 13, 15 ([roadmap](https://github.com/slolab/biotope-bench/blob/main/docs/roadmap.md))

## Goal

AHL can be used as a pinned dependency of another repository. A config file, an env file and the run directory can live anywhere. Images are built from Dockerfiles shipped inside the `ahl` package, with pinned harness versions, and every run records which AHL and which image it used. Four generic config keys cover what callers need next: read-only data mounts, joining an existing Docker network, non-secret environment variables, and installing a local package with extras.

## Scope

- Paths on the command line resolve against the current working directory. Paths inside the config file resolve against the config file's directory. Stop assuming the config sits in the AHL checkout.
- `--env-file` and `--runs-dir` options.
- Ship Dockerfiles and build contexts as package data.
- An `ahl build` command.
- Pin the Claude Code and OpenCode versions and the uv image. Label images with the harness and, where pinned, its version.
- Record AHL and image provenance in `session.json`.
- Config keys `mounts`, `network`, `env` and `packages[].extras`.

## Non-goals

- Headless execution (A2) and the Codex harness (A3).
- Pinning the Gemini, agy, Claude Science and DeepSeek harness versions beyond the shared uv image. DeepSeek is already pinned by its lockfile.
- Egress restriction, non-root containers, resource limits.
- Generating compose files.

## Design and interfaces

### Paths and env

- `load_config(config_path, env_file=None)`.
- Paths given on the command line (`-c`, `--env-file`, `--runs-dir`, and A2's `--turn`) resolve against the current working directory.
- Paths inside the config file (`workspace`, `capabilities[].path`, `packages[].path`, `mounts[].path`) resolve against the config file's directory, wherever the CLI is run from. This already holds for the existing keys; the tests pin it down for a config outside the checkout.
- The env file is `--env-file PATH` if given, otherwise `<config dir>/.env` if it exists. An `--env-file` path that does not exist raises a `ConfigError`. An explicit `--env-file` wins over variables already set in the shell, for the keys it defines. The implicit `<config dir>/.env`, used when `--env-file` is not given, keeps today's behaviour: shell variables win.
- A missing provider key produces an error that names the env file that was read, or says that none was read.

### Run directory

- `ahl up --runs-dir DIR` puts the run at `DIR/<run id>`. `--name` and `--resume` resolve inside `DIR`.
- The default stays `<config dir>/runs`.

### Images

- Move `docker/` to `src/ahl/images/`, keeping its internal layout: `<harness>.Dockerfile`, `init-firewall.sh`, `deepseek/`, `permissions/`. Resolve it with `importlib.resources`. The name `images` avoids clashing with the `ahl.docker` module.
- `dockerfile_path()` and the build context stop depending on `config.root`.
- New command `ahl build (--harness NAME | -c CONFIG)` builds `agent-harness-lab:<harness>`. With `-c` it reads only `harness` from the config. Building needs no provider key and reads no env file. `ahl up` keeps building by default and uses the same code path.
- Pins:
  - `CLAUDE_CODE_VERSION` and `OPENCODE_VERSION` default to exact versions, not `latest`;
  - every Dockerfile's uv stage uses an exact `ghcr.io/astral-sh/uv:<version>` tag;
  - use current stable releases at implementation time and record the chosen versions in the PR.
- Labels:
  - every image carries `ahl.harness=<name>`;
  - the claude, opencode and deepseek images also carry `ahl.harness.version=<version>`. For DeepSeek the version comes from its lockfile;
  - the unpinned images (gemini, agy, claude-science) have no `ahl.harness.version` label.

### Provenance in `session.json`

These fields are added and existing fields are kept:

```json
{
  "ahl": {"version": "0.1.0", "git_sha": "c1a4014…", "git_dirty": false},
  "image": {"name": "agent-harness-lab:opencode", "id": "sha256:…", "harness_version": "1.18.32"}
}
```

- `git_sha` and `git_dirty` are `null` when the package is not inside a git work tree.
- `image.id` and `image.harness_version` come from `docker image inspect` just before the container starts, also with `--no-build`.
- `image.harness_version` is `null` when the image has no `ahl.harness.version` label.

### New config keys

```yaml
mounts:                      # extra bind mounts; default read-only
  - path: ../datasets/YAGO2geo/unpacked   # host path, relative to the config file
    target: /workspace/data               # absolute container path
    readonly: true                        # optional, default true
network: my-compose-project_default       # optional; join this existing Docker network
env:                                      # optional; non-secret variables set in the container
  NEO4J_URI: bolt://neo4j:7687
packages:
  - name: mypkg
    install: mount
    path: ../mypkg
    extras: [graph]                       # optional; install as mypkg[graph]
```

- `mounts`: the source must exist and the target must be absolute. The target may not be `/workspace` itself, but may be a path under it. Duplicate targets are rejected.
- `network`: AHL checks the network exists (`docker network inspect`) before starting a container, and passes `--network <name>`.
- `env`: each entry is passed as `-e NAME=value`. Values must be strings. Names that AHL manages itself are rejected with a `ConfigError` naming the variable, before any container starts:
  - every provider key variable in `PROVIDER_KEY_ENV`;
  - every variable the selected harness adapter sets (its `build_env`, and from A2 its headless environment).
- `extras`: applies to both install modes and to both the `uv tool install` and `uv pip install` paths.
  - Setup commands run under `sh -c`, so the requirement is shell-quoted: `uv tool install --editable '/opt/ahl-packages/mypkg[graph]'`.
  - A package with a console script goes through `uv tool install`. Its extras then land in the tool's isolated environment: the package's own CLI sees them, the container's system `python3` does not.

## Acceptance criteria

- **AC-1** (unit) For a config file outside the AHL checkout, relative `workspace`, capability, package and mount paths resolve against the config file's directory, whatever the working directory is.
- **AC-2** (unit) Env file:
  - `--env-file PATH` loads that file, and a relative `PATH` resolves against the working directory;
  - an `--env-file` path that does not exist raises a `ConfigError` naming the path;
  - with `--env-file`, the file's value wins over a conflicting shell variable;
  - without the flag, `<config dir>/.env` is loaded if present, and the shell wins over it, as today;
  - the missing-key error names the env file read, or says none was read.
- **AC-3** (unit) `--runs-dir DIR` creates the run at `DIR/<run id>`. A relative `DIR` resolves against the working directory, not the config directory. `--name` and `--resume` resolve inside `DIR`. Without the flag, runs go to `<config dir>/runs`.
- **AC-4** (unit) Dockerfiles and build contexts resolve from the installed `ahl` package (`src/ahl/images/`), independent of the config directory and the working directory.
- **AC-5** (unit, docker) `ahl build`:
  - unit tier: `ahl build -c CONFIG`, with no provider key in the shell and no env file, runs `docker build` for the harness named in the config;
  - docker tier: `ahl build --harness claude` and `ahl build --harness opencode`, run from a directory outside the AHL checkout, build `agent-harness-lab:claude` and `agent-harness-lab:opencode`.
- **AC-6** (unit) Dockerfile pins and labels:
  - the Claude Code and OpenCode Dockerfiles have no `latest` defaults;
  - every Dockerfile pins the uv image to an exact version;
  - every Dockerfile sets `ahl.harness`;
  - the claude, opencode and deepseek Dockerfiles set `ahl.harness.version`, and the gemini, agy and claude-science Dockerfiles do not.
- **AC-7** (unit, docker) `session.json` contains `ahl.version`, `ahl.git_sha`, `ahl.git_dirty`, `image.name`, `image.id` and `image.harness_version`. With `docker image inspect` stubbed, an image without the version label gives `image.harness_version: null`. In the docker tier, `image.id` and `image.harness_version` match `docker image inspect` for the image, with and without `--no-build`.
- **AC-8** (unit) Each `mounts` entry adds `-v <absolute source>:<target>:ro`, or `-v <absolute source>:<target>` when `readonly: false`. A missing source, a relative target, the target `/workspace` and duplicate targets are each rejected with a `ConfigError` naming the entry.
- **AC-9** (unit) `network: <name>` adds `--network <name>` to the container arguments. A network that does not exist fails with a `ConfigError` before any container starts.
- **AC-10** (unit) `env: {NAME: value}` adds `-e NAME=value` to the container arguments. A non-string value, `OPENROUTER_API_KEY`, and a variable the selected adapter sets (`ANTHROPIC_BASE_URL` for `harness: claude` with `provider: openrouter`) are each rejected with a `ConfigError` naming the variable, before any container starts.
- **AC-11** (unit, docker) `packages[].extras` installs the package with those extras.
  - Unit tier: the generated install command contains the shell-quoted requirement (`'<container path>[graph]'`), for both the `uv tool install` and the `uv pip install` path.
  - Docker tier: a small fixture package without console scripts, whose extra pulls in one PyPI dependency, is installed, and the container's `python3` imports that dependency.
- **AC-12** (unit) Existing behaviour is preserved. Run from the AHL checkout with `./config.yaml`, `ahl up` reads `./.env` and writes to `./runs/`. All existing tests pass, changed only where they referenced the old `docker/` path.
- **AC-13** (unit) `README.md` and `config.example.yaml` document `--env-file`, `--runs-dir`, `ahl build`, `mounts`, `network`, `env` and `extras`. A test asserts each of these names appears in both files.

## Test plan

- Unit tests with `tmp_path` configs placed outside the checkout, and `monkeypatch.chdir` into an unrelated directory.
- Docker calls are mocked at the `subprocess` boundary for the unit tier, like the `docker_stub` fixture in `tests/test_permissions.py`.
- The `docker` and `live` pytest markers are registered in `pyproject.toml` and deselected by default, so `uv run pytest` needs neither Docker nor network access. `uv run pytest -m docker` runs the docker tier.
- Docker tier: tests that build the two images and one image with the fixture package.

## Risks and open questions

- The DeepSeek image runs `npm ci` against its lockfile inside the build context. Moving the context must keep that working. Build it once locally and note the result in the PR, even though DeepSeek is otherwise out of scope.
- Hatchling must include the non-Python files under `src/ahl/images/` in wheels. Check with `uv build` and list the wheel's contents.

## Evidence required in the PR

- The pinned versions chosen.
- `docker image inspect` label output for the claude and opencode images.
- A sample `session.json` from `ahl up` with the new fields.
- The wheel file listing showing `ahl/images/`.
- The result of building the DeepSeek image from the moved context.
- Output of `uv run pytest -m docker`.
