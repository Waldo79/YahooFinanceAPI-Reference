# Repository Maintenance Workflow

This workflow keeps the local repository and GitHub synchronized without repeating a full backup, cleanup, and fresh clone.

## Start a work session

From the repository root, run:

```text
scripts\start-work-session.cmd
```

The helper refuses to proceed when the working tree contains tracked changes or untracked files. It then switches to `main` and runs a fast-forward-only pull. It never creates a merge commit.

Create a separate branch for the work:

```text
git switch -c work/descriptive-branch-name
```

Examples include `work/study-08`, `work/study-07-v0.2`, or `work/v0.1.0-release`.

## Finish a work session

While still on the work branch, run:

```text
scripts\finish-work-session.cmd
```

The helper:

1. refuses to run on `main`;
2. checks the proposed diff for whitespace errors;
3. runs the complete pytest suite in a temporary folder; and
4. displays the files that remain to be reviewed.

It does not commit, push, merge, or delete anything automatically.

After reviewing the status, commit and push the work branch. Merge it into `main` only after the local tests and GitHub Quality Checks pass.

## What the automated hygiene test prevents

The repository test blocks tracked copies of:

- Python cache files and `__pycache__` folders;
- pytest working folders;
- private evidence under `captures/local`;
- duplicate utility trees under `tests/tools`; and
- the accidental nested path `observations/raw/observations`.

It also verifies that the corresponding `.gitignore` protections remain present.

## Private evidence backup

GitHub does not contain `captures/local`. Back up that folder separately after adding important captures. Ordinary tracked files are already protected by Git history and GitHub.

## Release milestones

Use a Git tag and GitHub Release for milestone snapshots such as `v0.1.0`. Keep unversioned canonical files as the current source of truth. Add version-suffixed working copies only when they have a specific continuing purpose.
