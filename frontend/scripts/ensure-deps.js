const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const frontendRoot = path.resolve(__dirname, '..');
const cliServiceEntry = path.join(
  frontendRoot,
  'node_modules',
  '@vue',
  'cli-service',
  'bin',
  'vue-cli-service.js',
);

if (fs.existsSync(cliServiceEntry)) {
  process.exit(0);
}

console.log('Installing frontend dependencies (first run)...');

const npmCmd = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const result = spawnSync(npmCmd, ['install'], {
  cwd: frontendRoot,
  stdio: 'inherit',
  shell: true,
});

if (result.status !== 0 || !fs.existsSync(cliServiceEntry)) {
  console.error('Dependency install failed. Run "npm install" inside frontend/ and retry.');
  process.exit(result.status || 1);
}
