import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import native from './next.config.native.mjs'

const require = createRequire(import.meta.url)
const dependencies = Object.keys(require('./package.json').dependencies)
// Keep server SDKs external during local evaluation to bound compiler memory.
const sdk = dependencies.filter((name) => /^@(aws-sdk|azure|google-cloud|google|opentelemetry|smithy|trigger\.dev|browserbasehq|linear|modelcontextprotocol|cerebras|a2a-js)\//.test(name)
  || ['googleapis', 'google-auth-library', 'openai', '@anthropic-ai/sdk', 'groq-sdk', 'ssh2'].includes(name))
if (native.output === 'standalone') throw new Error('The preview requires the full source and dependencies.')
export default {
  ...native,
  productionBrowserSourceMaps: false,
  enablePrerenderSourceMaps: false,
  outputFileTracingIncludes: {},
  outputFileTracingExcludes: { '**/*': ['**/*'] },
  async redirects() {
    return [{ source: '/', destination: '/workspace', permanent: false }, ...(await native.redirects?.() ?? [])]
  },
  webpack(config, { isServer, webpack }) {
    if (!isServer) {
      config.resolve.alias = { ...config.resolve.alias,
        '@sim/platform-authz/workspace$': '@sim/platform-authz/predicates',
        '@/lib/core/security/encryption$': fileURLToPath(new URL('./browser-password.mjs', import.meta.url)) }
      config.plugins.push(new webpack.DefinePlugin({ 'typeof globalThis.process': JSON.stringify('undefined') }))
    }
    config.resolve.alias = { ...config.resolve.alias,
      'monaco-editor$': 'monaco-editor/esm/vs/editor/editor.main.js' }
    config.externals = config.externals.map((external) => typeof external !== 'function' ? external
      : (data, callback) => {
        if (data.dependencyType === 'url' && data.request?.startsWith('pdfjs-dist/')) return callback()
        return external(data, callback)
      })
    config.cache = false
    config.devtool = false
    config.parallelism = 2
    config.optimization.minimize = false
    config.optimization.concatenateModules = false
    // shortcut: full-image preview needs no dependency trace; restore before standalone packaging.
    for (const plugin of config.plugins) {
      if (plugin?.constructor?.name === 'TraceEntryPointsPlugin') plugin.traceIgnores = ['**/*']
    }
    return config
  },
  serverExternalPackages: [...new Set([...native.serverExternalPackages, ...sdk])],
  experimental: { ...native.experimental, cpus: 1, webpackMemoryOptimizations: true,
    serverSourceMaps: false, serverMinification: false, webpackBuildWorker: true,
    parallelServerCompiles: false, turbopackFileSystemCacheForBuild: false,
    turbopackSourceMaps: false, turbopackInputSourceMaps: false,
    turbopackMinify: true, turbopackScopeHoisting: false },
}
