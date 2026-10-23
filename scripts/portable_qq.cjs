'use strict';
// Run only the downloaded Node package, with an explicit private QQ data path.
// Windows' native wrapper otherwise returns the user's daily Tencent Files path.
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

function inside(candidate, parent) {
  const rel = path.relative(parent, candidate);
  return rel !== '' && !rel.startsWith('..' + path.sep) && !path.isAbsolute(rel);
}

async function main() {
  const root = fs.realpathSync(process.env.WHALE_INSTALL_ROOT || path.join(__dirname, '..'));
  const client = fs.realpathSync(process.env.WHALE_QQ_CLIENT || path.join(root, '.runtime', 'qq'));
  const data = path.resolve(process.env.WHALE_QQ_DATA || path.join(root, 'data', 'qq-client'));
  if (!inside(client, root) || !inside(data, root)) throw new Error('QQ directories must belong to this installation');
  fs.mkdirSync(data, { recursive: true });
  if (!inside(fs.realpathSync(data), root)) throw new Error('QQ data directory resolves outside this installation');
  const wrapperPath = path.join(client, 'wrapper.node');
  const native = require(wrapperPath);
  if (typeof native.NodeQQNTWrapperUtil?.getNTUserDataInfoConfig !== 'function') {
    throw new Error('Unsupported QQ wrapper; refusing to use the daily QQ data directory');
  }
  // Do not alter a native read-only property. Override the module export locally
  // before NapCat loads it; the upstream binary and all other processes stay intact.
  const util = Object.create(native.NodeQQNTWrapperUtil);
  Object.defineProperty(util, 'getNTUserDataInfoConfig', { value: () => data });
  const isolated = Object.create(native);
  Object.defineProperty(isolated, 'NodeQQNTWrapperUtil', { value: util });
  require.cache[require.resolve(wrapperPath)].exports = isolated;
  if (require(wrapperPath).NodeQQNTWrapperUtil.getNTUserDataInfoConfig() !== data) {
    throw new Error('Could not isolate QQ data');
  }
  process.env.NAPCAT_WRAPPER_PATH = wrapperPath;
  process.env.NAPCAT_QQ_PACKAGE_INFO_PATH = path.join(client, 'package.json');
  process.env.NAPCAT_QQ_VERSION_CONFIG_PATH = path.join(client, 'config.json');
  process.env.NAPCAT_DISABLE_PIPE = '1';
  if (process.argv.includes('--check-isolation')) {
    console.log(JSON.stringify({ isolated: true }));
    process.exit(0);
  }
  process.chdir(client);
  await import(pathToFileURL(path.join(client, 'napcat', 'napcat.mjs')).href);
}

main().catch(() => {
  console.error('The isolated QQ runtime could not start. Check the local setup page.');
  process.exit(1);
});
