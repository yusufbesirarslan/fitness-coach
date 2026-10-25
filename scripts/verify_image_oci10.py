"""Verify all OCI blobs and manifest references; compare inspected loaded config."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile

root = Path(sys.argv[1])
with tarfile.open(root / 'target-image.oci.tar') as tar:
    members = {m.name: m for m in tar if m.isfile()}
    for name, member in members.items():
        if name.startswith('blobs/sha256/'):
            h = hashlib.sha256()
            with tar.extractfile(member) as f:
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(block)
            assert h.hexdigest() == name.split('/')[-1], name
    def read(name):
        return tar.extractfile(members[name]).read()
    def blob(desc):
        name = 'blobs/' + desc['digest'].replace(':', '/')
        b = read(name)
        assert len(b) == desc['size']
        return b
    def walk(desc):
        b = blob(desc)
        if 'manifest' in desc['mediaType'] or 'index' in desc['mediaType']:
            obj = json.loads(b)
            if 'manifests' in obj:
                for d in obj['manifests']: walk(d)
            if 'config' in obj: blob(obj['config'])
            for d in obj.get('layers', []): blob(d)
    index_bytes = read('index.json')
    index = json.loads(index_bytes)
    for d in index['manifests']: walk(d)
    manifests = index['manifests']
    if len(manifests) == 1 and 'index' in manifests[0]['mediaType']:
        manifests = json.loads(blob(manifests[0]))['manifests']
    image_desc = next(d for d in manifests if d.get('platform', {}).get('architecture') == 'amd64')
    image = json.loads(blob(image_desc))
    config = json.loads(blob(image['config']))
    loaded = json.loads((root / 'image-inspect.json').read_text())[0]
    assert loaded['Id'] == image['config']['digest']
    assert config['architecture'] == 'amd64' and config['os'] == 'linux'
    assert config['config']['Cmd'] == ['gunicorn', '--config', 'gunicorn.conf.py', 'starter:app']
    assert config['config']['User'] == 'appuser'
    runtime = json.loads((root / 'installed-runtime.json').read_text())
    expected = json.loads((root / 'source-files.json').read_text())
    assert runtime['files'] == expected
    attestations = []
    for d in manifests:
        if d == image_desc: continue
        m = json.loads(blob(d))
        for layer in m.get('layers', []):
            statement = json.loads(blob(layer))
            attestations.append(statement)
    assert any('provenance' in a.get('predicateType', '') for a in attestations)
    (root / 'provenance-statements.json').write_text(json.dumps(attestations, indent=2) + '\n')
    result = {'complete_image': True, 'all_blob_hashes_and_references_verified': True,
              'image_manifest_digest': image_desc['digest'], 'config_image_id': image['config']['digest'],
              'index_json_digest': 'sha256:' + hashlib.sha256(index_bytes).hexdigest(),
              'archive_sha256': hashlib.file_digest(open(root / 'target-image.oci.tar', 'rb'), 'sha256').hexdigest(),
              'platform': 'linux/amd64', 'config': config['config'],
              'loaded_config_matches_oci': True, 'target_files_match_source': True}
    (root / 'verified-image.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'config'}, indent=2))
