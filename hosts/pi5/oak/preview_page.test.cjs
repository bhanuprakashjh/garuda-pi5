const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('RGB preview follows JPEG freshness through idle depth, loss and recovery', async () => {
    const source = fs.readFileSync(path.join(__dirname, 'preview.py'), 'utf8');
    const script = source.match(/<script>([\s\S]*?)<\/script>/)[1];
    const elements = new Map(['demo', 'full', 'status', 'feed'].map(id => [id, {style: {}}]));
    let health;
    let tick;
    const context = {
        location: {protocol: 'http:', hostname: 'bench'},
        document: {getElementById: id => elements.get(id)},
        setInterval: callback => { tick = callback; },
        fetch: async () => {
            if (!health) throw new Error('offline');
            return {json: async () => health};
        }
    };

    await vm.runInNewContext(script, context);
    const feed = elements.get('feed');
    assert.equal(feed.style.visibility, 'hidden');
    health = {healthy: false, age_s: 0.05, error: null, usb: 'SUPER', fps: 15};
    await tick();
    assert.equal(feed.style.visibility, 'visible');
    assert.match(elements.get('status').textContent, /LIVE/);
    assert.match(feed.src, /^\/stream\?t=/);
    for (const age of [2, null]) {
        health.age_s = age;
        await tick();
        assert.equal(feed.style.visibility, 'hidden');
    }
    health.age_s = 0.1;
    health.error = 'Camera disconnected';
    await tick();
    assert.equal(feed.style.visibility, 'hidden');
    assert.equal(elements.get('status').textContent, health.error);
    health.error = null;
    await tick();
    assert.equal(feed.style.visibility, 'visible');
});
