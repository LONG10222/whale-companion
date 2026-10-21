"""Operator-owned paths. Private machine paths never belong in source control."""
from pathlib import Path
import json,os

ROOT=Path(__file__).resolve().parent

def workspace():
    return Path(os.environ.get('WHALE_WORKSPACE',str(ROOT/'workspace'))).expanduser().resolve()

def codex_project():
    file=ROOT/'configs/qq-workbench.local.json'
    cfg=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
    project=Path(os.environ.get('WHALE_CODEX_PROJECT_PATH',cfg.get('project_path',str(ROOT)))).expanduser().resolve()
    identity=os.environ.get('WHALE_CODEX_PROJECT_ID',cfg.get('source_project_id',cfg.get('local_project_id','')))
    return project,identity
