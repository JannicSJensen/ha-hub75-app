# Versioning and Updates

## Home Assistant Installation

Add `https://github.com/JannicSJensen/ha-hub75-app` to the repositories in
**Settings > Apps > App store**, then install **HUB75 Album Artwork**. On older
HA versions the same page is called **Add-ons**. The repository must be public
for installation without additional Git credentials.

The root `repository.yaml` identifies the repository. The `hub75/config.yaml`
manifest identifies the app, supported architectures, permissions, options, and
version. Since no `image` is specified, Supervisor builds `hub75/Dockerfile` on
the HA host. A GitHub Container Registry account is not needed.

Home Assistant Container and standalone Core do not have Supervisor or an app
store. This package is intended for Home Assistant OS, or another installation
that provides a compatible Supervisor.

## Installing Updates

HA detects app updates by comparing the installed version with the `version`
field in the repository's app manifest. Pushes, tags, and GitHub releases alone
do not make an installed app upgrade: the manifest version must increase.

Refresh the app store, open the app, and select **Update** when a newer version
appears. Supervisor rebuilds the image from the refreshed source and restarts
the app. Back up the app before updating. Check the Changelog tab for behavior
or configuration changes. No HA YAML edits or Core restart are needed.

## Releasing a Version

Use semantic versions, for example `0.1.1` for a compatible fix, `0.2.0` for new
functionality, and `1.0.0` for the first stable release.

1. Update `version` in `hub75/config.yaml` and add the matching entry to
   `hub75/CHANGELOG.md`. Update documentation when behavior changes.
2. Run `python -m unittest discover -s tests -v` with development dependencies
   installed, and verify `git diff --check`.
3. Commit the changes and push them to `main`.
4. Wait for **Test and Build** in GitHub Actions to pass. It runs manifest,
   behavior, and mock HTTP integration tests and builds amd64 and arm64 images.
5. Create a matching Git tag and GitHub release, such as `v0.1.1`, describing the
   changes. Tags record the release but are not used instead of the HA version.

Keep the app `slug` stable across versions; changing it creates a different app
identity. Keep the repository URL stable as well. Review architecture, option,
and permission changes before publishing.

## Validation Scope

CI checks packaging and container builds. It does not connect to a physical
HUB75 panel or an actual Supervisor installation. Before deploying to a new
controller, verify its 64x64 configuration and test real playback. A green CI
run is not a claim of completed hardware testing.

## Local Development Prerequisites

Python needs virtual-environment support. On Debian/Ubuntu, a missing
`ensurepip` usually means the matching `python3-venv` package is not installed.
Install it through your normal administrator workflow, then follow the root
README's development commands.

Docker commands require permission to use the Docker daemon. If local access
is unavailable, GitHub Actions performs both architecture builds. Do not change
Docker socket permissions or expose the daemon just to run a build.