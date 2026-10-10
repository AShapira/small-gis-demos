// Optional real-browser test. Install Playwright in .artifacts/browser first.
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../.artifacts/browser/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const output = process.env.BROWSER_OUTPUT || path.join(root, 'bundle', 'validation');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:8600';
(async () => {
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true});
  const context = await browser.newContext({viewport:{width:1280,height:850}});
  const external = [], errors = [], visited = [], unversioned = [];
  await context.route('**/*', route => {
    const url=new URL(route.request().url());
    if (url.origin !== new URL(base).origin) {
      external.push(route.request().url()); return route.abort();
    }
    if((url.pathname.endsWith('/wms')||url.pathname.endsWith('/service/wmts'))&&!url.searchParams.get('style_revision'))unversioned.push(url.href);
    return route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', e=>errors.push(e.message));
  await page.goto(base+'/geoserver/www/basemap/index.html', {waitUntil:'networkidle'});
  if(await page.locator('#language').inputValue()!=='en') throw Error('English must be the default language');
  async function screenshot(name, expectedLayer) {
    await page.waitForTimeout(600);
    await page.waitForLoadState('networkidle');
    try {
      await page.waitForFunction(()=>{
        if(document.querySelector('#map').dataset.loading!=='0')return false;
        const canvas=document.querySelector('#map canvas');if(!canvas)return false;
        const {width:w,height:h}=canvas;
        const pixels=canvas.getContext('2d').getImageData(0,0,w,h).data;
        // These opaque basemaps must cover the viewport. Waiting for network
        // idle alone can capture a frame before loaded tiles are composited.
        for(let y=10;y<h;y+=32)for(let x=10;x<w;x+=32)if(pixels[(y*w+x)*4+3]<250)return false;
        return true;
      },{},{timeout:60000});
    } catch(error) {
      await page.screenshot({path:path.join(output,'failed-'+name+'.png')});
      throw Error(name+': '+JSON.stringify(await page.locator('#map').evaluate(e=>({...e.dataset}))),{cause:error});
    }
    if ((await page.locator('#status').getAttribute('class'))==='error') throw Error(await page.locator('#status').innerText());
    const actual=await page.locator('#map').getAttribute('data-layer');
    if(actual!==expectedLayer) throw Error(`Expected ${expectedLayer}, received ${actual}`);
    visited.push({name,layer:actual});
    await page.screenshot({path:path.join(output,'viewer-'+name+'.png')});
  }
  for(const language of ['en','local']) {
    const suffix=language==='en'?'-en':'';
    await page.selectOption('#language',language);
    await page.selectOption('#service','WMTS');
    await page.click('[data-place=prague]');
    for (const style of ['osm-bright','positron','dark-matter','toner','maptiler-basic']) {
      await page.selectOption('#style',style);
      await screenshot(style+suffix,'omt:'+style+suffix);
    }
    await page.selectOption('#style','osm-bright');
    await page.selectOption('#service','WMS');
    await page.click('[data-place=bratislava]');
    await screenshot('wms-bratislava'+suffix,'omt:osm-bright'+suffix);
    await page.selectOption('#style','world');
    await page.click('[data-place=world]');
    await screenshot('world'+suffix,'omt:world'+suffix);
  }
  const report={external_requests:external,page_errors:errors,unversioned_map_requests:unversioned,status:await page.locator('#status').innerText(),screenshots:visited.length,visited_layers:visited};
  fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify(report,null,2));
  await browser.close();
  if(external.length||errors.length||unversioned.length||report.status.includes('failed')) throw Error(JSON.stringify(report));
  console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exit(1)});
