from pathlib import Path

from install_skill import compare, doctor, install, tree_hash


def _source(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "references").mkdir()
    (source / "SKILL.md").write_text("---\nname: research-anything\ndescription: x\n---\n", encoding="utf-8")
    (source / "scripts" / "runtime.py").write_text("x = 1\n", encoding="utf-8")
    (source / "scripts" / "test_runtime.py").write_text("raise AssertionError\n", encoding="utf-8")
    (source / "references" / "rule.md").write_text("rule\n", encoding="utf-8")
    return source


def test_install_and_compare_runtime_bundle(tmp_path):
    source = _source(tmp_path)
    target = tmp_path / "skills" / "research-anything"
    result = install(source, target)
    assert result["changed"] is True and result["in_sync"] is True
    assert not (target / "scripts" / "test_runtime.py").exists()
    assert tree_hash(source) == tree_hash(target)
    assert compare(source, target)["in_sync"] is True


def test_force_install_keeps_backup(tmp_path):
    source = _source(tmp_path)
    target = tmp_path / "skills" / "research-anything"
    install(source, target)
    (source / "scripts" / "runtime.py").write_text("x = 2\n", encoding="utf-8")
    result = install(source, target, force=True)
    assert result["in_sync"] is True
    assert result["backup"] and Path(result["backup"]).is_dir()


def test_doctor_separates_installed_from_license_authorized(tmp_path, monkeypatch):
    source = _source(tmp_path)
    target = tmp_path / "skills" / "research-anything"
    tools = tmp_path / "tools"
    (tools / "MediaCrawler").mkdir(parents=True)
    monkeypatch.setenv("RESEARCH_TOOLS_DIR", str(tools))
    monkeypatch.delenv("RESEARCH_USAGE_MODE", raising=False)
    default = doctor(source, target)["connectors"]["mediacrawler"]
    assert default["installed"] is True
    assert default["available"] is False
    monkeypatch.setenv("RESEARCH_USAGE_MODE", "personal-noncommercial")
    authorized = doctor(source, target)["connectors"]["mediacrawler"]
    assert authorized["installed"] is True
    assert authorized["available"] is True
