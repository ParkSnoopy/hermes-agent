// Rewrite relative Markdown links as content-root paths for Docusaurus.

const path = require('path');

const MARKDOWN_LINK = /^(\.\.?\/[^?#]*\.mdx?)([?#].*)?$/i;

function contentRoot(sourceFilePath, siteDir) {
  return path.posix.join(siteDir.split(path.sep).join('/'), 'docs');
}

function walk(node, visitor) {
  if (!node || typeof node !== 'object') {
    return;
  }
  if (node.type === 'link' || node.type === 'definition') {
    visitor(node);
  }
  if (Array.isArray(node.children)) {
    node.children.forEach((child) => walk(child, visitor));
  }
}

function plugin(options = {}) {
  const siteDir = options.siteDir || process.cwd();
  return (root, file) => {
    if (!file.path) {
      return;
    }
    const sourceDir = path.posix.dirname(file.path.split(path.sep).join('/'));
    const root_ = contentRoot(file.path, siteDir);
    walk(root, (node) => {
      const match = MARKDOWN_LINK.exec(node.url || '');
      if (!match) {
        return;
      }
      const resolved = path.posix.normalize(path.posix.join(sourceDir, match[1]));
      const relToRoot = path.posix.relative(root_, resolved);
      if (relToRoot.startsWith('../')) {
        return; // points outside the docs tree; leave it to Docusaurus
      }
      node.url = '/' + relToRoot + (match[2] || '');
    });
  };
}

module.exports = plugin;
