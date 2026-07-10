# Repository hygiene patch

Upload the extracted contents to the `repository-hygiene-fixes` branch at the repository root.

This patch:

- adds a root `.gitignore`;
- disables blank GitHub issues;
- removes the circular Issues contact link by replacing `config.yml`;
- documents the purpose of `observations/confirmed/`.

After upload, manually delete any tracked files inside `__pycache__` folders and any `.pyc` or `.pyo` files.
