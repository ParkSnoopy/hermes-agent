"""Commit admission retains trust and source identity without Desktop workflows."""
import json
import subprocess

import pytest

from scripts.releases.commit_build import admit, publish_receipt, receipt_tag
from scripts.releases.versioning import tag_record
from tests.ci.test_desktop_release_tag_admission import _git, _seed_repo


def environment(commit):
    return {
        'TAG': '', 'BUILD_COMMIT': commit, 'RELEASE_PHASE': '', 'UPLOAD_RELEASE': 'false',
        'DEFAULT_BRANCH': 'main', 'GITHUB_REF': 'refs/heads/main',
        'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REPOSITORY': 'fixture/repo',
        'GITHUB_WORKFLOW_REF': 'fixture/repo/.github/workflows/termux-release.yml@refs/heads/main',
        'GITHUB_ACTOR': 'maintainer', 'GITHUB_TRIGGERING_ACTOR': 'maintainer',
    }


def test_mixed_inputs_rejected_before_controller_runs():
    def forbidden(*args):
        raise AssertionError('rejected input reached the controller')

    for change in ({'TAG': 'v1.2.3'}, {'RELEASE_PHASE': 'candidate'}, {'UPLOAD_RELEASE': 'true'},
                   {'TERMUX_UPGRADE_FROM_TAG': 'v1.2.2'}, {'BUILD_COMMIT': 'abc123'},
                   {'BUNDLE_ENV_JSON': '{}'}):
        with pytest.raises(ValueError):
            admit({**environment('a' * 40), **change}, run=forbidden)


def test_pushed_feature_commit_admitted_without_switching_checkout(tmp_path):
    origin, clone = _seed_repo(tmp_path)
    main = _git('rev-parse', 'HEAD', cwd=clone)
    _git('checkout', '-qb', 'feature', cwd=origin)
    (origin / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="3.2.1"\n', encoding='utf-8')
    _git('add', 'pyproject.toml', cwd=origin)
    _git('commit', '-qm', 'feature', cwd=origin)
    commit = _git('rev-parse', 'HEAD', cwd=origin)
    _git('fetch', 'origin', cwd=clone)
    permissions = []

    def run(argv, repo=None):
        if argv[:2] == ['gh', 'api']:
            permissions.append(argv[2])
            return 'write'
        return subprocess.check_output(argv, cwd=repo, text=True, encoding='utf-8').strip()

    assert admit(environment(commit), run=run, repo=clone) == {
        'sha': commit, 'channel': 'commit', 'payload-version': '3.2.1'}
    assert permissions and set(permissions) == {'repos/fixture/repo/collaborators/maintainer/permission'}
    assert _git('rev-parse', 'HEAD', cwd=clone) == main
    assert not _git('tag', '--list', cwd=clone)
    with pytest.raises(ValueError):
        admit({**environment(commit), 'GITHUB_WORKFLOW_REF': 'fixture/other'}, run=run, repo=clone)


def test_receipts_bind_kind_commit_and_run_without_same_second_collisions(tmp_path):
    _origin, clone = _seed_repo(tmp_path)
    commit = _git('rev-parse', 'HEAD', cwd=clone)
    created_at = '2026-09-22T01:23:45Z'
    assert receipt_tag('commit', '0.0.0', created_at, '123') == 'v0.0.0+commit.20260922T012345Z.123'
    assert receipt_tag('commit', '0.0.0', created_at, '124') != receipt_tag('commit', '0.0.0', created_at, '123')

    def run(argv, repo=None):
        if argv[:2] == ['gh', 'api'] and argv[-1] == '.permission':
            return 'write'
        if argv[:2] == ['gh', 'api'] and '/actions/runs/' in argv[2]:
            run_id = argv[2].rsplit('/', 1)[-1]
            return json.dumps({'id': int(run_id), 'event': 'workflow_dispatch', 'status': 'in_progress',
                               'head_branch': 'main', 'head_sha': commit, 'created_at': created_at})
        return subprocess.check_output(argv, cwd=repo or clone, text=True, encoding='utf-8').strip()

    base = {**environment(commit), 'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': commit, 'GITHUB_RUN_ID': '123'}
    first = publish_receipt('commit', base, version='0.0.0', commit=commit,
                            details={'bundleEnv': {}}, run=run, repo=clone)
    second = publish_receipt('commit', {**base, 'GITHUB_RUN_ID': '124'}, version='0.0.0', commit=commit,
                             details={'bundleEnv': {}}, run=run, repo=clone)
    assert [first['tag'], second['tag']] == ['v0.0.0+commit.20260922T012345Z.123',
                                             'v0.0.0+commit.20260922T012345Z.124']
    assert tag_record(_git('tag', '-l', first['tag'], '--format=%(contents)', cwd=clone)) == first
    assert _git('rev-parse', f"{first['tag']}^{{commit}}", cwd=clone) == commit
    assert publish_receipt('commit', base, version='0.0.0', commit=commit,
                           details={'bundleEnv': {}}, run=run, repo=clone) == first