// Run inside the pinned AHL DeepSeek image; no API credentials or network needed.
import assert from 'node:assert/strict';
import { Context } from '/opt/deepseek/node_modules/@deepseek-ai/cordis/lib/index.js';
import SystemPrompt from '/opt/deepseek/node_modules/@deepseek-ai/dsh-system-prompt/lib/index.js';
import Tools, { defineContentToolFixture } from '/opt/deepseek/node_modules/@deepseek-ai/dsh-tools/lib/index.js';
import { createScope } from '/opt/deepseek/node_modules/@deepseek-ai/dsh-scope/lib/index.js';
import * as policy from '/opt/ahl/permissions/deepseek.mjs';

const ctx = new Context();
new SystemPrompt(ctx, {});
new Tools(ctx);
const counters = { web_search: 0, web_fetch: 0, read: 0 };
for (const name of Object.keys(counters)) {
  ctx.tools.register(defineContentToolFixture({
    name, description: name, parameters: {},
    async execute() { counters[name]++; return [{type: 'text', text: 'executed'}]; },
  }));
}
// Normal native policy allows cannot override a global guard.
ctx.on('tools/pre-execute', async () => ({kind: 'allow'}));
const parent = {id: 'parent'};
const child = {id: 'child'};
const parentScope = createScope(ctx, parent);
const childScope = createScope(ctx, child, {parent});
childScope.ctx.on('tools/pre-execute', async () => ({kind: 'allow'}));
let seq = 0;
async function call(name, agent, nested) {
  return ctx.tools.execute({name, agent, callId: `test-${++seq}`, arguments: {},
    signal: new AbortController().signal, ...(nested ? {parent: Symbol('ptc-parent')} : {})});
}
for (const deny of [['web_search', 'web_fetch'], ['web_fetch'], []]) {
  const fork = ctx.plugin(policy, {deny});
  await fork.await();
  for (const mode of ['native', 'ptc', 'both']) {
    const disposeMode = parentScope.ctx.tools.presentAs(mode);
    for (const agent of [parent, child]) {
      for (const name of Object.keys(counters)) {
        const before = counters[name];
        const result = await call(name, agent, mode === 'ptc');
        if (deny.includes(name)) {
          assert.equal(result.isError, true);
          assert.match(result.content[0].text, new RegExp(`Denied by AHL permissions: ${name}`));
          assert.equal(counters[name], before, 'denied tool body executed');
        } else {
          assert.ok(!result.isError, JSON.stringify(result));
          assert.equal(counters[name], before + 1);
        }
      }
    }
    disposeMode();
  }
  await fork.dispose();
}
// The policy itself must not grant Minimal any tools.
const empty = new Context();
new SystemPrompt(empty, {});
new Tools(empty);
const emptyFork = empty.plugin(policy, {deny: ['web_search', 'web_fetch']});
await emptyFork.await();
assert.deepEqual(empty.tools.schemas(), []);
await emptyFork.dispose();
await childScope.dispose();
await parentScope.dispose();

console.log(JSON.stringify({result: 'passed', counters, modes: ['native', 'ptc nested dispatch', 'both'], scopes: ['parent', 'child'], policies: 3}));
