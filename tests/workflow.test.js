// Exercise the exact github-script block without any GitHub/network writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const workflow = fs.readFileSync(path.join(__dirname, '../.github/workflows/generate-card.yml'), 'utf8');
const script = workflow.split('script: |')[1].split(/\r?\n/).map(line => line.slice(12)).join('\n');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const execute = new AsyncFunction('require', 'context', 'github', 'core', script);
const generated = Buffer.from('<svg>sample</svg>');

test('live card publication requires an explicit manual dispatch', () => {
  const triggers = workflow.split(/\r?\non:\r?\n/)[1].split(/\r?\npermissions:/)[0];
  assert.match(triggers, /^  workflow_dispatch:/m);
  assert.doesNotMatch(triggers, /^  (?:push|schedule|pull_request):/m);
});

async function run(getContent) {
  const writes = [];
  const github = { rest: { repos: {
    getContent,
    createOrUpdateFileContents: async payload => writes.push(payload),
  } } };
  await execute(
    () => ({ readFileSync: () => generated }),
    { repo: { owner: 'repository-owner', repo: 'profile-cards' }, ref: 'refs/heads/preview' },
    github,
    { info: () => {} },
  );
  return writes;
}

test('unchanged card creates no commit', async () => {
  const writes = await run(async () => ({ data: { content: generated.toString('base64') + '\n', sha: 'old-sha' } }));
  assert.deepEqual(writes, []);
});

test('changed card updates the repository owner and selected branch', async () => {
  const writes = await run(async request => {
    assert.equal(request.owner, 'repository-owner');
    assert.equal(request.ref, 'preview');
    return { data: { content: 'old-content', sha: 'old-sha' } };
  });
  assert.equal(writes.length, 1);
  assert.equal(writes[0].owner, 'repository-owner');
  assert.equal(writes[0].branch, 'preview');
  assert.equal(writes[0].sha, 'old-sha');
});

test('missing card is created without a sha', async () => {
  const writes = await run(async () => { throw Object.assign(new Error('not found'), { status: 404 }); });
  assert.equal(writes.length, 1);
  assert.equal(writes[0].sha, undefined);
});

test('permission failures are not treated as a missing card', async () => {
  await assert.rejects(run(async () => { throw Object.assign(new Error('forbidden'), { status: 403 }); }), /forbidden/);
});
