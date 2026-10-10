/* All map resources use this GeoServer's origin. No public tile/glyph service. */
(async()=>{
  const cfg=await (await fetch('config.json',{cache:'no-store'})).json();
  const base=new URL('../../',location.href).pathname.replace(/\/$/,'');
  const status=document.getElementById('status');
  const style=document.getElementById('style');
  const language=document.getElementById('language');
  style.value=cfg.default_style;
  language.value=cfg.default_language||'en';
  const countryNames={'europe/czech-republic':'Czechia','europe/slovakia':'Slovakia'};
  const snapshot='20'+cfg.snapshot.slice(0,2)+'-'+cfg.snapshot.slice(2,4)+'-'+cfg.snapshot.slice(4,6);
  document.getElementById('coverage').textContent=cfg.countries.map(x=>countryNames[x]||x.split('/').pop()).join(' · ')+' · '+snapshot;
  const extent=ol.proj.get('EPSG:3857').getExtent();
  const resolutions=Array.from({length:cfg.maxzoom+1},(_,z)=>ol.extent.getWidth(extent)/256/2**z);
  const grid=new ol.tilegrid.WMTS({origin:ol.extent.getTopLeft(extent),resolutions,matrixIds:resolutions.map((_,z)=>'EPSG:3857:'+z)});
  const layer=new ol.layer.Tile();
  const map=new ol.Map({target:'map',layers:[layer],view:new ol.View({center:ol.proj.fromLonLat([17.2,49]),zoom:6,maxZoom:cfg.maxzoom})});
  let activeSourceKey;
  function update(){
    const name='omt:'+style.value+(language.value==='en'?'-en':'');
    document.getElementById('map').dataset.layer=name;
    const wmts=document.getElementById('service').value==='WMTS';
    const revision='?style_revision='+encodeURIComponent(cfg.style_revisions?.[name.slice(4)]||'initial');
    const sourceKey=(wmts?'WMTS:':'WMS:')+name+revision;
    if(sourceKey===activeSourceKey)return;
    activeSourceKey=sourceKey;
    const source=wmts?new ol.source.WMTS({url:base+'/gwc/service/wmts'+revision,layer:name,matrixSet:'EPSG:3857',format:'image/png',style:'',tileGrid:grid,wrapX:true}):new ol.source.TileWMS({url:base+'/wms'+revision,params:{LAYERS:name,VERSION:'1.3.0',FORMAT:'image/png',TILED:false},projection:'EPSG:3857',gutter:64});
    let pending=0, failures=0;
    const updateStatus=()=>{
      if(layer.getSource()!==source)return;
      document.getElementById('map').dataset.loading=String(pending);
      status.className=failures?'error':'';
      status.textContent=failures?'Map request failed — check GeoServer logs.':pending?'Loading map…':(wmts?'WMTS':'WMS')+' · '+style.options[style.selectedIndex].text+' · '+(language.value==='en'?'English only':'Local labels')+' · zoom '+map.getView().getZoom().toFixed(1);
    };
    source.on('tileloadstart',()=>{pending++;updateStatus()});
    source.on('tileloaderror',()=>{pending=Math.max(0,pending-1);failures++;updateStatus()});
    source.on('tileloadend',()=>{pending=Math.max(0,pending-1);updateStatus()});
    layer.setSource(source);
    document.getElementById('map').dataset.loading='1';
  }
  const views={world:[0,20,2],countries:[17.2,49,6],prague:[14.421,50.087,15],bratislava:[17.108,48.146,15]};
  document.querySelectorAll('[data-place]').forEach(b=>b.onclick=()=>{if(b.dataset.place==='countries'&&cfg.bounds){map.getView().fit(ol.proj.transformExtent(cfg.bounds,'EPSG:4326','EPSG:3857'),{padding:[40,40,40,40],duration:400});return}const [lon,lat,zoom]=views[b.dataset.place];map.getView().animate({center:ol.proj.fromLonLat([lon,lat]),zoom,duration:400})});
  style.onchange=update;language.onchange=update;document.getElementById('service').onchange=update;update();
})().catch(e=>{document.getElementById('status').textContent=e.message;document.getElementById('status').className='error'});
