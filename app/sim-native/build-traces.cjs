const { createRequire } = require('node:module')
const path = require('node:path')
const native = createRequire(path.join(process.cwd(), 'package.json'))
if (native('next/package.json').version !== '16.4.1') throw new Error('Unsupported native preview compiler version.')
const target = native.resolve('next/dist/build/collect-build-traces')
// shortcut: the full-source preview needs no packaging trace; remove before standalone production builds.
require.cache[target] = { id: target, filename: target, loaded: true,
  exports: { collectBuildTraces: async () => console.log('Dependency packaging trace omitted for the full-source internal preview.') } }
