def test_required_release_files_exist():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    required = [
        "README.md",
        "CHANGELOG.md",
        "MANIFEST.csv",
        "data/master_field_database.csv",
        "data/repository_structure.csv",
        "data/release_workflow.csv",
        "templates/release_checklist_template.csv",
    ]
    missing = [p for p in required if not (root / p).exists()]
    assert not missing
