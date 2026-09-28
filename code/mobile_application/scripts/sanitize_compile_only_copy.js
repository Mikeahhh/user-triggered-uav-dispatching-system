#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const root = process.argv[2];
if (!root || !path.isAbsolute(root)) {
  throw new Error('Expected an absolute temporary project path');
}

const sourceRoots = ['pages', 'services']
  .map(name => path.join(root, name))
  .filter(candidate => fs.existsSync(candidate));
const sourceExtensions = new Set(['.js', '.jsx', '.ts', '.tsx']);
const liveDatabasePattern =
  /https:\/\/[A-Za-z0-9.-]+\.(?:firebaseio\.com|firebasedatabase\.app)/g;
let changedFiles = 0;

const visit = candidate => {
  const stat = fs.statSync(candidate);
  if (stat.isDirectory()) {
    for (const name of fs.readdirSync(candidate)) visit(path.join(candidate, name));
    return;
  }
  if (!sourceExtensions.has(path.extname(candidate))) return;
  const original = fs.readFileSync(candidate, 'utf8');
  const sanitized = original.replace(
    liveDatabasePattern,
    'https://compile-only.invalid',
  );
  if (sanitized !== original) {
    fs.writeFileSync(candidate, sanitized);
    changedFiles += 1;
  }
};

for (const sourceRoot of sourceRoots) visit(sourceRoot);

const manifest = path.join(root, 'android/app/src/main/AndroidManifest.xml');
if (fs.existsSync(manifest)) {
  const original = fs.readFileSync(manifest, 'utf8');
  const sanitized = original.replace(
    /(android:name="com\.google\.android\.geo\.API_KEY"\s+android:value=")[^"]+("\s*\/?>)/,
    '$1compile-only-placeholder$2',
  );
  if (sanitized !== original) fs.writeFileSync(manifest, sanitized);
}

for (const sourceRoot of sourceRoots) {
  const assertSanitized = candidate => {
    const stat = fs.statSync(candidate);
    if (stat.isDirectory()) {
      for (const name of fs.readdirSync(candidate)) {
        assertSanitized(path.join(candidate, name));
      }
      return;
    }
    if (!sourceExtensions.has(path.extname(candidate))) return;
    const text = fs.readFileSync(candidate, 'utf8');
    if (liveDatabasePattern.test(text)) {
      throw new Error(`Live Firebase URL remains in ${path.relative(root, candidate)}`);
    }
    liveDatabasePattern.lastIndex = 0;
  };
  assertSanitized(sourceRoot);
}

process.stdout.write(`Sanitized ${changedFiles} source file(s) for compile-only build.\n`);
