import assert from 'node:assert/strict';

// 检查开场、战斗和暂停三个状态；不能只在关闭开场层后验证页面宽度。
export async function checkExperience(browser, url, output) {
 const viewports = [
  {width: 320, height: 740, mobile: true},
  {width: 390, height: 844, mobile: true},
  {width: 768, height: 1024, mobile: true},
  {width: 844, height: 390, mobile: true},
  {width: 1280, height: 720, mobile: false},
  {width: 1440, height: 1000, mobile: false}
 ];
 for (const {width, height, mobile} of viewports) {
  const context = await browser.newContext({viewport: {width, height}, isMobile: mobile, hasTouch: mobile});
  const page = await context.newPage();
  try {
   await page.addInitScript(() => { Math.random = () => .625; });
   await page.clock.install({time: new Date('2026-01-01T00:00:00Z')});
   await page.clock.pauseAt(new Date('2026-01-01T00:00:01Z'));
   await page.goto(url);
   await page.clock.runFor(32);
   const layout = await page.evaluate(() => ({
    width: innerWidth,
    scroll: document.documentElement.scrollWidth,
    hud: document.querySelector('.hud').getBoundingClientRect().height,
    headerClickable: [...document.querySelectorAll('header a')].every(link => getComputedStyle(link).pointerEvents !== 'none')
   }));
   assert.ok(layout.width <= width + 1 && layout.scroll <= width + 1, `Ready layout must fit ${width}px: ${JSON.stringify(layout)}`);
   assert.ok(layout.hud <= 170, `HUD must remain compact at ${width}px`);
   assert.ok(layout.headerClickable, 'Header links must be pointer accessible');
   const start = page.getByRole('button', {name: '开始游戏 →', exact: true});
   await start.scrollIntoViewIfNeeded();
   const hit = await start.evaluate(button => {
    const rect = button.getBoundingClientRect();
    const overlay = document.querySelector('#overlay').getBoundingClientRect();
    const target = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return rect.height >= 44 && rect.top >= overlay.top && rect.bottom <= overlay.bottom + 1 && button.contains(target);
   });
   assert.ok(hit, `Start button must be fully visible and actually clickable at ${width}px`);
   if (width === 390 || width === 1440) await page.screenshot({path: `${output}/ready-${mobile ? 'mobile' : 'desktop'}.png`, fullPage: true});
   await start.click();
   await page.clock.runFor(32);
   assert.equal(await page.evaluate(() => window.starharbor.snapshot().phase), 'playing');
   assert.ok((await page.locator('#shield-status').textContent()).includes('就绪'));
   assert.equal(await page.locator('#shield-btn').isEnabled(), true);

   if (width === 390 || width === 1440) {
    // 真正发送键盘输入；不通过修改 snapshot 或直接改状态绕过操作层。
    await page.keyboard.press('Space');
    await page.clock.runFor(64);
    const activated = await page.evaluate(() => window.starharbor.snapshot());
    assert.equal(activated.shield.active, true, 'Space must activate the core shield');
    assert.ok(activated.shield.cooldown > 3, 'Shield activation must start the real cooldown');
    assert.equal(await page.locator('#shield-btn').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('#shield-btn').isDisabled(), true);
    await page.clock.runFor(1500);
    const cooling = await page.evaluate(() => window.starharbor.snapshot());
    assert.equal(cooling.shield.active, false, 'Shield must expire after 1.2 seconds');
    assert.ok(cooling.shield.cooldown > 0);
    assert.ok((await page.locator('#shield-status').textContent()).includes('冷却'));
    await page.keyboard.press('Shift');
    await page.clock.runFor(32);
    assert.equal(await page.evaluate(() => window.starharbor.snapshot().shield.active), false, 'Cooldown must reject Shift reactivation');
    await page.getByRole('button', {name: '暂停游戏', exact: true}).click();
    await page.clock.runFor(32);
    const paused = await page.evaluate(() => window.starharbor.snapshot());
    await page.clock.runFor(1000);
    const stillPaused = await page.evaluate(() => window.starharbor.snapshot());
    assert.equal(stillPaused.time, paused.time);
    assert.equal(stillPaused.shield.cooldown, paused.shield.cooldown, 'Pause must freeze shield cooldown');
    await page.getByRole('button', {name: '继续游戏', exact: true}).click();
    await page.clock.runFor(3000);
    assert.equal(await page.evaluate(() => window.starharbor.snapshot().shield.cooldown), 0);
    assert.ok((await page.locator('#shield-status').textContent()).includes('就绪'));
    // 手机 tap 和桌面 click 走同一条真实输入路径。
    if (mobile) await page.getByRole('button', {name: '启动护盾', exact: true}).tap();
    else await page.getByRole('button', {name: '启动护盾', exact: true}).click();
    await page.clock.runFor(32);
    assert.equal(await page.evaluate(() => window.starharbor.snapshot().shield.active), true, 'Shield button must activate through core.step');
    await page.clock.runFor(4100);
    await page.keyboard.press('Shift');
    await page.clock.runFor(32);
    assert.equal(await page.evaluate(() => window.starharbor.snapshot().shield.active), true, 'Shift must activate when cooldown finishes');
    await page.clock.runFor(1500);
    assert.equal(await page.evaluate(() => window.starharbor.snapshot().shield.active), false, 'Shift must not create permanent immunity');
   }
   await page.getByRole('button', {name: '暂停游戏', exact: true}).click();
   await page.clock.runFor(32);
   assert.equal(await page.locator('#pause-message').isVisible(), true);
   assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
   if (width === 390) {
    // 手机结算页也要真实进入并点击重开，不能只测开场和桌面重开。
    await page.reload();
    await page.clock.runFor(32);
    await page.getByRole('button', {name: '开始游戏 →', exact: true}).click();
    await page.clock.runFor(3000);
    await page.keyboard.down('w');
    await page.keyboard.down('a');
    await page.clock.runFor(2440);
    await page.keyboard.up('w');
    await page.keyboard.up('a');
    for (let second = 0; second < 30; second++) {
     await page.clock.runFor(1000);
     if (await page.evaluate(() => window.starharbor.snapshot().phase === 'over')) break;
    }
    assert.equal(await page.evaluate(() => window.starharbor.snapshot().phase), 'over');
    const restart = page.getByRole('button', {name: '再次出航 →', exact: true});
    await restart.scrollIntoViewIfNeeded();
    assert.equal(await restart.evaluate(button => {
     const r = button.getBoundingClientRect();
     return button.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
    }), true, 'Mobile restart must be actually clickable');
    assert.equal(await page.evaluate(() => innerWidth <= 391 && document.documentElement.scrollWidth <= 391), true);
    await page.screenshot({path: `${output}/over-mobile.png`, fullPage: true});
    await restart.click();
    await page.clock.runFor(32);
    const restarted = await page.evaluate(() => window.starharbor.snapshot());
    assert.equal(restarted.phase, 'playing');
    assert.equal(restarted.hp, 100);
    assert.equal(restarted.shield.cooldown, 0);
    assert.equal(await page.locator('#pause').getAttribute('aria-pressed'), 'false');
   }
  } finally { await context.close(); }
 }
 console.log('PASS: six viewport entry layouts, start/restart hit targets, keyboard/touch shield, cooldown, pause freeze and mobile game over');
}
