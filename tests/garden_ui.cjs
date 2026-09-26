// NODE_PATH=/path/to/jsdom/node_modules node tests/garden_ui.cjs
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '..');
const dom = new JSDOM('<div id="garden-root"></div><section id="gdn-viewer-section"></section><dialog id="gd-lightbox"></dialog>', { runScripts:'outside-only', url:'http://localhost/garden' });
const w = dom.window;
w.HTMLElement.prototype.scrollIntoView = function(){};
w.HTMLDialogElement.prototype.showModal = function(){this.open=true;};
w.HTMLDialogElement.prototype.close = function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
const requests=[];
w.Image=class {naturalWidth=200;naturalHeight=100;set src(url){requests.push(url);queueMicrotask(()=>url.includes('canna')?this.onerror():this.onload());}};
function load(file){w.eval(fs.readFileSync(path.join(root,file),'utf8'));}
load('static/js/core.js');load('static/js/plant-assets.js');load('static/js/garden-tags.js');
const d={ text:{},totals:{beds:2,plants:1,species:1,photos:1},
 beds:[{id:'b1',name:'Front bed',plants:1,photos:1,color:'#526c48',species:[{id:'salvia-farinacea',name:'Salvia'}]}, {id:'b2',name:'Back bed',plants:0,photos:1,color:'#526c48',species:[]}],
 species:[{id:'salvia-farinacea',name:'Salvia',photos:1}],
 plantings:[{id:'p1',bed:'b1',species:'salvia-farinacea',name:'My salvia'}],
 photos:[{id:'f1',url:'/photo.jpg',caption:'Flowers',date:'2024-04-03',beds:['b1','b2'],species:['salvia-farinacea'],plantings:['p1'],tags:['spring-blooms']}]};
w.VW.getJSON=async()=>d;w.GDN={Viewer:{init(){},focusBed(){}}};load('static/js/garden-page.js');
(async()=>{
 await w.VW.GardenPage.onEnter();
 assert.equal([...w.document.getElementById('garden-root').children].at(-1).id,'gd-photos');
 assert.equal(w.document.querySelectorAll('#gd-photos .garden-tag').length,4);
 w.dispatchEvent(new w.CustomEvent('garden:plant-selected',{detail:{plantingId:'p1'}}));
 assert.match(w.document.getElementById('gd-plant-detail').textContent,/My salvia/);
 assert.equal(w.document.querySelectorAll('#gd-plant-detail img').length,2);
 assert.equal(w.document.querySelectorAll('#gd-plant-detail a').length,0);
 const editor=w.VW.GardenTags.editor();w.document.body.append(editor.el);
 const input=editor.el.querySelector('input[type=text]');input.value='##Spring Blooms';input.dispatchEvent(new w.Event('input'));
 assert.equal(input.value,'Spring Blooms');input.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',cancelable:true}));
 assert.equal(JSON.stringify(editor.values()),JSON.stringify(['spring-blooms']));
 const event=new w.InputEvent('beforeinput',{data:'#',cancelable:true});input.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
 editor.el.querySelector('[aria-label="Remove tag spring-blooms"]').click();assert.equal(editor.values().length,0);
 await Promise.all([w.VW.PlantArt.preload(['salvia-farinacea','canna','unknown']),w.VW.PlantArt.preload(['salvia-farinacea'])]);assert.equal(requests.length,2);
 const calls=[], ctx=Object.fromEntries(['drawImage','beginPath','arc','fill','stroke','setTransform','clearRect'].map(n=>[n,(...a)=>calls.push([n,...a])]));
 w.VW.PlantArt.draw(ctx,'salvia-farinacea',50,60,40);assert.deepEqual(calls[0].slice(2),[30,50,40,20]);
 calls.length=0;w.VW.PlantArt.draw(ctx,'canna',0,0,40);assert.equal(calls[0][0],'beginPath');
 Object.defineProperty(w,'devicePixelRatio',{value:3});const canvas={width:0,height:0,getContext:()=>ctx};
 w.VW.PlantArt.canvasContext(canvas,390,340);assert.equal(canvas.width,1170);assert.equal(canvas.height,1020);
 w.testing={h:w.VW.h,api(){},card(){},pageHead(){},tabs(){},empty(){},toast(){},run(){},dialog(){},editable(){},saveAll(){},confirmDelete(){},
   field(label,name,opts={}){const input=w.document.createElement('input');input.name=name;input.value=opts.value||'';return input;},
   values(form){return Object.fromEntries(new w.FormData(form));},
   photoPicker(){return {el:w.document.createElement('div'),files:()=>[new w.File(['x'],'test.jpg',{type:'image/jpeg'})]};}};
 let source=fs.readFileSync(path.join(root,'static/app/screens/site-garden.js'),'utf8').replace(/^import .*;$/gm,'').replace('export async function render','async function render');
 w.eval('const { '+Object.keys(w.testing).join(',')+' } = window.testing;\n'+source+'\nwindow.photoFieldsForTest=photoFields;');
 const fields=w.photoFieldsForTest(d,d.plantings[0]);w.document.body.append(fields.form);
 [...fields.form.querySelectorAll('button')].find(b=>b.textContent==='#back-bed').click();
 const payload=fields.payload();assert.deepEqual([...payload.getAll('beds')],['b1','b2']);assert.deepEqual([...payload.getAll('plantings')],['p1']);assert.equal(payload.get('taken_on'),'');
 console.log('PASS: gallery order, hashtag pills/input, plant photo detail, upload origin/multiple beds, cache/fallback/geometry, DPR');
})().catch(e=>{console.error(e);process.exitCode=1;});
