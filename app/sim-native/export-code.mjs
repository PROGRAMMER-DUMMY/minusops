import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

export function fileHash(filename) {
  const hash = crypto.createHash('sha256')
  const handle = fs.openSync(filename, 'r')
  const buffer = Buffer.allocUnsafe(1024 * 1024)
  try {
    let size
    while ((size = fs.readSync(handle, buffer, 0, buffer.length, null))) hash.update(buffer.subarray(0, size))
    return hash.digest('hex')
  } finally { fs.closeSync(handle) }
}

export function snapshot(root) {
  const inputs = ['bun.lock', 'package.json', 'apps/sim/package.json', 'node_modules/next/package.json']
  const runtime = { node: process.version, platform: process.platform, arch: process.arch,
    files: Object.fromEntries(inputs.map((name) => [name, fileHash(path.join(root, name))])) }
  const files = []
  function walk(relative) {
    for (const item of fs.readdirSync(path.join(root, 'apps/sim', relative), { withFileTypes: true })) {
      if (relative === '.next' && item.name === 'cache') continue
      const name = path.posix.join(relative, item.name)
      const full = path.join(root, 'apps/sim', name)
      if (item.isDirectory()) walk(name)
      else {
        assert.ok(item.isFile(), 'Compiled links are refused')
        files.push({ path: name, bytes: fs.statSync(full).size, sha256: fileHash(full) })
      }
    }
  }
  walk('.next')
  return { runtime, files: files.sort((a, b) => a.path.localeCompare(b.path)) }
}

export function compare(current, baseline) {
  assert.deepEqual(current.runtime, baseline.runtime, 'Runtime dependency fingerprint changed')
  for (const inventory of [current.files, baseline.files]) {
    assert.ok(Array.isArray(inventory) && inventory.length > 0 && inventory.length <= 15000)
    const seen = new Set()
    for (const file of inventory) {
      assert.equal(typeof file.path, 'string')
      assert.ok(file.path.startsWith('.next/') && !file.path.includes('\\') && !/[\x00-\x1f\x7f]/.test(file.path))
      assert.ok(file.path.split('/').every((part) => part !== '..' && part !== '.' && part !== ''))
      assert.ok(!seen.has(file.path.toLowerCase()), 'Duplicate compiled path')
      assert.ok(Number.isSafeInteger(file.bytes) && file.bytes >= 0 && file.bytes <= 256 * 1024 ** 2)
      assert.match(file.sha256, /^[a-f0-9]{64}$/)
      seen.add(file.path.toLowerCase())
    }
    assert.ok(inventory.reduce((sum, file) => sum + file.bytes, 0) <= 8 * 1024 ** 3)
  }
  const old = new Map(baseline.files.map((file) => [file.path, file]))
  const names = new Set(current.files.map((file) => file.path))
  return {
    changed: current.files.filter((file) => old.get(file.path)?.sha256 !== file.sha256 || old.get(file.path)?.bytes !== file.bytes),
    removed: baseline.files.filter((file) => !names.has(file.path)).map((file) => file.path),
  }
}

export function exportCode(root, output, baselinePath, revision, expectedBaseline) {
  assert.match(revision, /^[a-f0-9]{40}$/)
  assert.match(expectedBaseline, /^[a-f0-9]{64}$/)
  assert.equal(fileHash(baselinePath), expectedBaseline)
  assert.ok(fs.statSync(baselinePath).size <= 4 * 1024 ** 2)
  const baseline = JSON.parse(fs.readFileSync(baselinePath, 'utf8'))
  assert.equal(baseline.version, 1)
  assert.match(baseline.base_ci_source, /^[a-f0-9]{40}$/)
  assert.match(baseline.base_ci_config, /^sha256:[a-f0-9]{64}$/)
  const current = snapshot(root)
  const delta = compare(current, baseline)
  const archive = path.join(output, 'native-code.tar.gz')
  const metadataPath = path.join(output, 'native-code-metadata.json')
  const list = path.join(output, 'native-code-files.list')
  for (const file of [archive, metadataPath, list]) assert.equal(fs.existsSync(file), false)
  fs.writeFileSync(list, delta.changed.map((file) => file.path + '\0').join(''), { flag: 'wx' })
  try {
    const result = spawnSync('tar', ['--create', '--gzip', '--file', archive,
      '--directory', path.join(root, 'apps/sim'), '--null', '--files-from', list],
    { timeout: 300000, maxBuffer: 8192, env: { ...process.env, GZIP: '-1' } })
    assert.equal(result.status, 0, 'Compiled archive creation failed')
  } finally { fs.unlinkSync(list) }
  const overlays = JSON.parse(fs.readFileSync(path.join(root, '../overlay/patch-manifest.json'), 'utf8'))
  assert.ok(Array.isArray(overlays.files) && overlays.files.length > 0 && overlays.files.length <= 128)
  for (const file of overlays.files) {
    assert.match(file.path, /^(apps\/(sim|realtime)\/|packages\/workflow-renderer\/src\/)/)
    assert.ok(!file.path.split('/').some((part) => part === '..' || part === '.' || part === 'ee'))
    assert.equal(fileHash(path.join(root, file.path)), file.sha256, 'Native overlay source changed')
  }
  const metadata = { version: 1, source_revision: revision, baseline_sha256: expectedBaseline,
    base_ci_source: baseline.base_ci_source, base_ci_config: baseline.base_ci_config,
    runtime: current.runtime, files: current.files, changed: delta.changed, removed: delta.removed,
    overlay_files: overlays.files, archive: 'native-code.tar.gz', archive_bytes: fs.statSync(archive).size,
    archive_sha256: fileHash(archive), changed_bytes: delta.changed.reduce((sum, file) => sum + file.bytes, 0) }
  assert.ok(metadata.archive_bytes <= 1024 ** 3)
  fs.writeFileSync(metadataPath, JSON.stringify(metadata), { flag: 'wx' })
  console.log(`NATIVE_CODE_METADATA_SHA256=${fileHash(metadataPath)}`)
  console.log(`NATIVE_CODE_ARCHIVE_SHA256=${metadata.archive_sha256}`)
  console.log(JSON.stringify({ files: current.files.length, changed_files: delta.changed.length,
    changed_bytes: metadata.changed_bytes, archive_bytes: metadata.archive_bytes }))
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  assert.equal(process.argv.length, 4)
  exportCode('/app', process.argv[2], process.argv[3], process.env.SOURCE_REVISION, process.env.BASELINE_SHA256)
}
